package xmod;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.SharedPreferences;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageInfo;
import android.content.pm.PackageInstaller;
import android.net.Uri;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.util.Log;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.ArrayList;
import java.util.List;

/**
 * Self-update from GitHub Releases (repo / token baked in at build time, see Cfg).
 *
 * Release layout (made by release.py):
 *   tag "v<modVersion>", assets: base.apk, split_config.*.apk, xmod-release.json
 *   xmod-release.json = {"mod_version": N, "x_version": "12.41.0", "x_version_code": 312410001}
 * If the X version is unchanged only base.apk is downloaded and installed with MODE_INHERIT_EXISTING.
 */
final class Updater {
    private static final String TAG = "XMod.Updater";
    private static final String ACTION = "xmod.UPDATE_STATUS";
    private static final long AUTO_INTERVAL_MS = 12L * 60 * 60 * 1000;
    private static final Handler main = new Handler(Looper.getMainLooper());
    private static volatile boolean busy;

    private Updater() {}

    static boolean configured() {
        return Cfg.REPO != null && !Cfg.REPO.isEmpty();
    }

    static String versionLabel() {
        return "X Mod v" + Cfg.MOD_VERSION + " (X " + Cfg.X_VERSION + ")";
    }

    // ---------------------------------------------------------------- checking

    static final class Release {
        int version;
        String tag, notes, xVersion;
        long xVersionCode;
        final List<String[]> assets = new ArrayList<>();   // {name, api url, size}
    }

    /** Called from onActivityResumed; checks at most every 12 h. */
    static void maybeAutoCheck(final Activity a, SharedPreferences prefs, boolean enabled) {
        if (!enabled || !configured() || busy || prefs == null) return;
        long now = System.currentTimeMillis();
        if (now - prefs.getLong("upd_last_check", 0) < AUTO_INTERVAL_MS) return;
        prefs.edit().putLong("upd_last_check", now).apply();
        check(a, prefs, false);
    }

    static void check(final Activity a, final SharedPreferences prefs, final boolean manual) {
        if (!configured()) {
            if (manual) Toast.makeText(a, "Updates are not configured in this build", Toast.LENGTH_LONG).show();
            return;
        }
        if (busy) return;
        busy = true;
        if (manual) Toast.makeText(a, "Checking for updates…", Toast.LENGTH_SHORT).show();
        new Thread(() -> {
            Release r = null;
            String error = null;
            try {
                r = fetchLatest();
            } catch (Throwable t) {
                Log.w(TAG, "check", t);
                error = t.getMessage();
            }
            final Release rel = r;
            final String err = error;
            main.post(() -> {
                busy = false;
                if (a.isFinishing() || a.isDestroyed()) return;
                if (rel == null) {
                    if (manual) Toast.makeText(a, "Update check failed: " + err, Toast.LENGTH_LONG).show();
                } else if (rel.version > Cfg.MOD_VERSION) {
                    if (!manual && prefs != null && prefs.getInt("upd_skip", 0) == rel.version) return;
                    offer(a, prefs, rel);
                } else if (manual) {
                    Toast.makeText(a, "You are up to date (" + versionLabel() + ")", Toast.LENGTH_LONG).show();
                }
            });
        }, "xmod-update-check").start();
    }

    private static Release fetchLatest() throws Exception {
        String json = new String(get("https://api.github.com/repos/" + Cfg.REPO + "/releases/latest",
                "application/vnd.github+json"), "UTF-8");
        JSONObject o = new JSONObject(json);
        Release r = new Release();
        r.tag = o.optString("tag_name", "");
        r.version = parseVersion(r.tag);
        r.notes = o.optString("body", "");
        JSONArray as = o.optJSONArray("assets");
        String meta = null;
        for (int i = 0; as != null && i < as.length(); i++) {
            JSONObject x = as.getJSONObject(i);
            String name = x.optString("name");
            r.assets.add(new String[]{name, x.optString("url"), String.valueOf(x.optLong("size"))});
            if (name.equals("xmod-release.json")) meta = x.optString("url");
        }
        if (meta != null) {
            JSONObject m = new JSONObject(new String(get(meta, "application/octet-stream"), "UTF-8"));
            r.xVersion = m.optString("x_version", "");
            r.xVersionCode = m.optLong("x_version_code", 0);
            if (r.version <= 0) r.version = m.optInt("mod_version", 0);
        }
        return r;
    }

    private static int parseVersion(String tag) {
        try {
            return Integer.parseInt(tag.replaceAll("[^0-9]", ""));
        } catch (Exception e) {
            return 0;
        }
    }

    // ---------------------------------------------------------------- UI

    private static void offer(final Activity a, final SharedPreferences prefs, final Release r) {
        String notes = r.notes == null ? "" : r.notes.trim();
        if (notes.length() > 1200) notes = notes.substring(0, 1200) + "…";
        boolean sameX = sameXVersion(a, r);
        String size = humanSize(sameX ? assetSize(r, "base.apk") : totalSize(a, r));
        new AlertDialog.Builder(Mod.themed(a))
                .setTitle("X Mod update: " + r.tag)
                .setMessage("Installed: " + versionLabel() + "\nAvailable: v" + r.version
                        + (r.xVersion != null && !r.xVersion.isEmpty() ? " (X " + r.xVersion + ")" : "")
                        + "\nDownload: " + size + "\n\n" + notes)
                .setPositiveButton("Update", (d, w) -> download(a, r))
                .setNeutralButton("Skip this version", (d, w) -> {
                    if (prefs != null) prefs.edit().putInt("upd_skip", r.version).apply();
                })
                .setNegativeButton("Later", null)
                .show();
    }

    private static boolean sameXVersion(Context c, Release r) {
        try {
            PackageInfo pi = c.getPackageManager().getPackageInfo(c.getPackageName(), 0);
            long code = Build.VERSION.SDK_INT >= 28 ? pi.getLongVersionCode() : pi.versionCode;
            return r.xVersionCode != 0 && r.xVersionCode == code;
        } catch (Exception e) {
            return false;
        }
    }

    /** Asset names to download: base.apk, plus the installed config splits when the X version changes. */
    private static List<String> wanted(Context c, Release r) {
        List<String> names = new ArrayList<>();
        names.add("base.apk");
        if (sameXVersion(c, r)) return names;
        ApplicationInfo ai = c.getApplicationInfo();
        if (ai.splitNames != null) {
            for (String s : ai.splitNames) names.add("split_" + s + ".apk");
        }
        return names;
    }

    private static String[] asset(Release r, String name) {
        for (String[] a : r.assets) if (a[0].equals(name)) return a;
        return null;
    }

    private static long assetSize(Release r, String name) {
        String[] a = asset(r, name);
        return a == null ? 0 : Long.parseLong(a[2]);
    }

    private static long totalSize(Context c, Release r) {
        long t = 0;
        for (String n : wanted(c, r)) t += assetSize(r, n);
        return t;
    }

    private static String humanSize(long b) {
        return b <= 0 ? "?" : String.format(java.util.Locale.US, "%.1f MB", b / 1048576.0);
    }

    // ---------------------------------------------------------------- download + install

    private static void download(final Activity a, final Release r) {
        if (Build.VERSION.SDK_INT >= 26 && !a.getPackageManager().canRequestPackageInstalls()) {
            Toast.makeText(a, "Allow X Mod to install updates, then tap Update again", Toast.LENGTH_LONG).show();
            a.startActivity(new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                    Uri.parse("package:" + a.getPackageName())));
            return;
        }
        final List<String> names = wanted(a, r);
        for (String n : names) {
            if (asset(r, n) == null) {
                Toast.makeText(a, "Release " + r.tag + " has no " + n, Toast.LENGTH_LONG).show();
                return;
            }
        }
        LinearLayout box = new LinearLayout(a);
        box.setOrientation(LinearLayout.VERTICAL);
        int pad = (int) (20 * a.getResources().getDisplayMetrics().density);
        box.setPadding(pad, pad, pad, pad);
        final TextView label = new TextView(a);
        label.setText("Downloading…");
        final ProgressBar bar = new ProgressBar(a, null, android.R.attr.progressBarStyleHorizontal);
        bar.setMax(1000);
        box.addView(label);
        box.addView(bar);
        final AlertDialog dlg = new AlertDialog.Builder(Mod.themed(a))
                .setTitle("Updating X Mod").setView(box).setCancelable(false).show();
        final long total = Math.max(1, totalSize(a, r));
        busy = true;

        new Thread(() -> {
            PackageInstaller pi = a.getPackageManager().getPackageInstaller();
            int sessionId = -1;
            try {
                PackageInstaller.SessionParams p = new PackageInstaller.SessionParams(
                        PackageInstaller.SessionParams.MODE_INHERIT_EXISTING);
                p.setAppPackageName(a.getPackageName());
                if (Build.VERSION.SDK_INT >= 31) {
                    p.setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED);
                }
                sessionId = pi.createSession(p);
                PackageInstaller.Session s = pi.openSession(sessionId);
                long done = 0;
                for (String n : names) {
                    String[] as = asset(r, n);
                    final String msg = "Downloading " + n + "…";
                    main.post(() -> label.setText(msg));
                    try (OutputStream out = s.openWrite(n, 0, Long.parseLong(as[2]))) {
                        done = stream(as[1], out, done, total, bar);
                        s.fsync(out);
                    }
                }
                main.post(() -> label.setText("Installing…"));
                registerResult(a);
                Intent i = new Intent(ACTION).setPackage(a.getPackageName());
                int flags = PendingIntent.FLAG_UPDATE_CURRENT | (Build.VERSION.SDK_INT >= 31 ? PendingIntent.FLAG_MUTABLE : 0);
                PendingIntent pend = PendingIntent.getBroadcast(a, sessionId, i, flags);
                s.commit(pend.getIntentSender());
                s.close();
                main.post(dlg::dismiss);
            } catch (Throwable t) {
                Log.e(TAG, "download", t);
                if (sessionId != -1) {
                    try { pi.abandonSession(sessionId); } catch (Throwable ignored) { }
                }
                final String m = String.valueOf(t.getMessage());
                main.post(() -> {
                    dlg.dismiss();
                    Toast.makeText(a, "Update failed: " + m, Toast.LENGTH_LONG).show();
                });
            } finally {
                busy = false;
            }
        }, "xmod-update-download").start();
    }

    private static BroadcastReceiver receiver;

    private static synchronized void registerResult(final Activity a) {
        if (receiver != null) return;
        final Context app = a.getApplicationContext();
        receiver = new BroadcastReceiver() {
            @Override public void onReceive(Context c, Intent intent) {
                int status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE);
                if (status == PackageInstaller.STATUS_PENDING_USER_ACTION) {
                    Intent confirm = intent.getParcelableExtra(Intent.EXTRA_INTENT);
                    if (confirm != null) {
                        confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                        app.startActivity(confirm);
                    }
                } else if (status != PackageInstaller.STATUS_SUCCESS) {
                    Toast.makeText(app, "Update not installed: "
                            + intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE), Toast.LENGTH_LONG).show();
                }
            }
        };
        IntentFilter f = new IntentFilter(ACTION);
        if (Build.VERSION.SDK_INT >= 33) app.registerReceiver(receiver, f, Context.RECEIVER_NOT_EXPORTED);
        else app.registerReceiver(receiver, f);
    }

    // ---------------------------------------------------------------- HTTP

    private static HttpURLConnection open(String url, String accept, boolean auth) throws IOException {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setInstanceFollowRedirects(false);       // never forward the token to the storage host
        c.setConnectTimeout(20000);
        c.setReadTimeout(60000);
        c.setRequestProperty("User-Agent", "xmod-updater");
        c.setRequestProperty("Accept", accept);
        if (auth && Cfg.TOKEN != null && !Cfg.TOKEN.isEmpty()) {
            c.setRequestProperty("Authorization", "Bearer " + Cfg.TOKEN);
            c.setRequestProperty("X-GitHub-Api-Version", "2022-11-28");
        }
        return c;
    }

    /** Opens url, following redirects manually (auth only on api.github.com). */
    private static HttpURLConnection connect(String url, String accept) throws IOException {
        boolean auth = true;
        for (int hop = 0; hop < 5; hop++) {
            HttpURLConnection c = open(url, accept, auth);
            int code = c.getResponseCode();
            if (code / 100 == 3) {
                String loc = c.getHeaderField("Location");
                c.disconnect();
                if (loc == null) throw new IOException("redirect without location");
                auth = Uri.parse(loc).getHost() != null && Uri.parse(loc).getHost().equals("api.github.com");
                url = loc;
                continue;
            }
            if (code != 200) {
                c.disconnect();
                throw new IOException("HTTP " + code + (code == 404 ? " (repo/token?)" : ""));
            }
            return c;
        }
        throw new IOException("too many redirects");
    }

    private static byte[] get(String url, String accept) throws IOException {
        HttpURLConnection c = connect(url, accept);
        try (InputStream in = c.getInputStream()) {
            ByteArrayOutputStream bo = new ByteArrayOutputStream();
            byte[] buf = new byte[16384];
            int n;
            while ((n = in.read(buf)) > 0) bo.write(buf, 0, n);
            return bo.toByteArray();
        } finally {
            c.disconnect();
        }
    }

    private static long stream(String url, OutputStream out, long done, long total, final ProgressBar bar) throws IOException {
        HttpURLConnection c = connect(url, "application/octet-stream");
        try (InputStream in = c.getInputStream()) {
            byte[] buf = new byte[65536];
            int n;
            long lastUi = 0;
            while ((n = in.read(buf)) > 0) {
                out.write(buf, 0, n);
                done += n;
                long now = System.currentTimeMillis();
                if (now - lastUi > 200) {
                    lastUi = now;
                    final int p = (int) (done * 1000 / total);
                    main.post(() -> bar.setProgress(p));
                }
            }
        } finally {
            c.disconnect();
        }
        return done;
    }
}
