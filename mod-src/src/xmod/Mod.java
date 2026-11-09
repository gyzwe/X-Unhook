package xmod;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.Application;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.res.Configuration;
import android.graphics.Typeface;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.Process;
import android.os.SystemClock;
import android.util.Log;
import android.util.TypedValue;
import android.view.ContextThemeWrapper;
import android.view.View;
import android.widget.CompoundButton;
import android.widget.LinearLayout;
import android.widget.RadioButton;
import android.widget.RadioGroup;
import android.widget.ScrollView;
import android.widget.Switch;
import android.widget.TextView;
import android.widget.Toast;

import com.x.database.core.impl.view.a;
import com.x.models.g5;
import com.x.models.gh;
import com.x.models.i6;
import com.x.models.q8;
import com.x.models.s1;
import com.x.models.v0;
import com.x.models.v4;
import com.x.models.y4;
import com.x.models.timelines.items.a1;
import com.x.models.timelines.items.b2;
import com.x.models.timelines.items.d1;
import com.x.models.timelines.items.j0;
import com.x.models.timelines.items.j1;
import com.x.models.timelines.items.m0;
import com.x.models.timelines.items.p1;
import com.x.models.timelines.items.y1;
import com.x.navigation.gt;
import com.x.navigation.qt;
import com.x.repositories.urt.b0;
import com.x.urt.paging.bottom.h;

import java.lang.ref.WeakReference;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * X Mod. Hooks (inserted via smali, see patch.py):
 *  - XApplication.onCreate                         -> init(app)
 *  - util.b.W(row, ...) entry                      -> enterRow(row)       (remembers timeline_id of the DB row)
 *  - util.b.W(...) call sites                      -> filterItem(item)    (DB row -> UrtTimelineItem)
 *  - urt.paging.bottom.h.a(LoadMore)               -> blockBottomPaging(pager)
 *  - FeatureSwitchesRepositoryImpl.get/peekBoolean -> fsBool(key)
 *  - home.tabbed.r.e(OnLogoClicked)                -> onLogoClick()
 *  - video.tab.h2.<init>(.., pinnedPost, playlist) -> videoPlaylist(pinnedPost, playlist)
 *  - account.b0.<init>(userId, ...)                -> ownAccount(userId)
 */
public final class Mod {
    private static final String TAG = "XMod";
    private static final String PREFS = "xmod_prefs";

    // ---- boolean settings -------------------------------------------------------------
    static final int HIDE_ADS = 0, HIDE_PREROLL = 1, HIDE_VIDEOS = 2, SINGLE_VIDEO = 3,
            LIMIT_FEED = 4, LIMIT_OTHER = 5, FOLLOWING_ONLY = 6, FOLLOWING_NO_REPOSTS = 7,
            HIDE_PHOTOS = 8, DISABLE_HOME = 9;
    static final String[] KEYS = {"hide_ads", "hide_preroll", "hide_videos", "single_video",
            "limit_feed", "limit_other", "following_only", "following_no_reposts", "hide_photos", "disable_home"};
    static final boolean[] DEFAULTS = {true, true, false, false, false, false, false, false, false, false};
    static final String[] TITLES = {
            "Hide ads",
            "Remove video pre-roll ads",
            "Hide videos in feeds",
            "Don't swipe to the next video",
            "Disable infinite scrolling",
            "Also in Explore and Video",
            "Following: only accounts I follow",
            "…and no reposts",
            "Hide photos in feeds",
            "Turn off the Home feed",
    };
    static final String[] SUBS = {
            "Promoted posts, promoted accounts, promoted trends, banners and Google ads",
            "Ads that play before videos in your feed",
            "Posts with videos are not shown in For you, Following, Explore, Lists and Communities "
                    + "(profiles, search and replies are not affected)",
            "A video opened from a post plays on its own: swiping up does not move on to recommended videos",
            "Home (For you, Following): stop loading older posts when you reach the bottom. "
                    + "Pull down to refresh for new posts.",
            "Apply the same limit to For you in Explore and to the Video tab",
            "Only posts from accounts you follow (and your own). No Who to follow, "
                    + "topics, prompts or recommendations.",
            "Hide reposts of other people's posts in Following",
            "Posts with photos are not shown in For you, Following, Explore, Lists and Communities "
                    + "(profiles, search and replies are not affected)",
            "For you and Following stay empty: nothing is loaded or shown. "
                    + "Search, profiles, notifications and messages keep working.",
    };

    static final boolean[] val = new boolean[KEYS.length];
    static final boolean[] locked = new boolean[KEYS.length];
    static volatile boolean lockMode = false;

    static final int[] LIMITS = {0, 50, 100, 200};        // index 0 = strictest
    static final String[] LIMIT_LABELS = {"the first page", "~50 items", "~100 items", "~200 items"};
    static volatile int limitIdx = 0;
    static volatile int lockedLimitIdx = -1;              // -1 = limit not locked

    // ---- runtime state ----------------------------------------------------------------
    private static SharedPreferences prefs;
    private static WeakReference<Activity> topActivity = new WeakReference<>(null);
    private static Handler main;
    private static Context appCtx;
    private static long lastLogoTap, lastEndToast;
    private static final Set<Long> ownIds = Collections.synchronizedSet(new HashSet<Long>());
    private static final ThreadLocal<String> curTimeline = new ThreadLocal<>();
    // safety valve for the "following only" filter
    private static volatile int followKept, followDropped;
    private static volatile boolean followFailOpen;

    static {
        for (int i = 0; i < KEYS.length; i++) val[i] = DEFAULTS[i];
    }

    private Mod() {}

    // =================================================================== init

    public static void init(Application app) {
        try {
            appCtx = app.getApplicationContext();
            main = new Handler(Looper.getMainLooper());
            prefs = app.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
            load();
            app.registerActivityLifecycleCallbacks(new Application.ActivityLifecycleCallbacks() {
                @Override public void onActivityCreated(Activity a, Bundle b) {}
                @Override public void onActivityStarted(Activity a) {}
                @Override public void onActivityResumed(Activity a) {
                    topActivity = new WeakReference<>(a);
                    maybeShowHint(a);
                }
                @Override public void onActivityPaused(Activity a) {}
                @Override public void onActivityStopped(Activity a) {}
                @Override public void onActivitySaveInstanceState(Activity a, Bundle b) {}
                @Override public void onActivityDestroyed(Activity a) {
                    if (topActivity.get() == a) topActivity = new WeakReference<>(null);
                }
            });
        } catch (Throwable t) {
            Log.e(TAG, "init failed", t);
        }
    }

    private static void load() {
        if (prefs == null) return;
        lockMode = prefs.getBoolean("lock_mode", false);
        for (int i = 0; i < KEYS.length; i++) {
            locked[i] = prefs.getBoolean("locked_" + KEYS[i], false);
            val[i] = locked[i] || prefs.getBoolean(KEYS[i], DEFAULTS[i]);
        }
        limitIdx = idxOfLimit(prefs.getInt("feed_limit", 0));
        lockedLimitIdx = prefs.getInt("locked_limit_idx", -1);
        if (lockedLimitIdx >= 0 && limitIdx > lockedLimitIdx) limitIdx = lockedLimitIdx;
        try {
            for (String s : prefs.getStringSet("own_ids", Collections.<String>emptySet())) {
                ownIds.add(Long.parseLong(s));
            }
        } catch (Throwable ignored) {
        }
    }

    private static void save() {
        if (prefs == null) return;
        SharedPreferences.Editor e = prefs.edit();
        for (int i = 0; i < KEYS.length; i++) {
            e.putBoolean(KEYS[i], val[i]);
            e.putBoolean("locked_" + KEYS[i], locked[i]);
        }
        e.putInt("feed_limit", LIMITS[limitIdx]);
        e.putInt("locked_limit_idx", lockedLimitIdx);
        e.putBoolean("lock_mode", lockMode);
        e.apply();
    }

    private static int idxOfLimit(int limit) {
        for (int i = 0; i < LIMITS.length; i++) if (LIMITS[i] == limit) return i;
        return 0;
    }

    private static void maybeShowHint(Activity a) {
        if (prefs == null || prefs.getBoolean("hint_shown", false)) return;
        prefs.edit().putBoolean("hint_shown", true).apply();
        Toast.makeText(a, "X Mod: double-tap the X logo on Home to open the mod menu",
                Toast.LENGTH_LONG).show();
    }

    /** account.b0 (AccountData) constructor: remember ids of logged-in accounts ("own" posts). */
    public static void ownAccount(Object userId) {
        try {
            if (!(userId instanceof gh)) return;
            long id = ((gh) userId).a;
            if (ownIds.add(id) && prefs != null) {
                Set<String> s = new HashSet<>(prefs.getStringSet("own_ids", Collections.<String>emptySet()));
                s.add(Long.toString(id));
                prefs.edit().putStringSet("own_ids", s).apply();
            }
        } catch (Throwable t) {
            Log.w(TAG, "ownAccount", t);
        }
    }

    // =================================================================== timeline items

    /** Entry of util.b.W(row, ...): remember which timeline the row belongs to. */
    public static void enterRow(Object row) {
        String id = null;
        try {
            a r = (a) row;
            if (r != null && r.a != null && r.a.a != null) id = r.a.a.a;
        } catch (Throwable ignored) {
        }
        curTimeline.set(id);
    }

    static final String[] HOME_TIMELINES = {"FOR_YOU", "FOLLOWING", "RANKED_FOLLOWING", "HOME_SUBSCRIBED"};

    /** urt.b0.p(direction, cursor): every network fetch of a URT timeline goes through it. true = skip. */
    public static boolean blockFetch(Object repo) {
        if (!val[DISABLE_HOME]) return false;
        try {
            Object type = ((b0) repo).a;
            String name = type == null ? "" : ((Enum<?>) type).name();
            for (String h : HOME_TIMELINES) if (h.equals(name)) return true;
        } catch (Throwable t) {
            Log.w(TAG, "blockFetch", t);
        }
        return false;
    }

    private static boolean tlStarts(String tl, String... prefixes) {
        if (tl == null) return false;
        for (String p : prefixes) if (tl.startsWith(p)) return true;
        return false;
    }

    /** Called with every UrtTimelineItem produced from the timeline DB. Returning null drops the item. */
    public static Object filterItem(Object item) {
        if (item == null) return null;
        try {
            String tl = curTimeline.get();
            boolean following = tlStarts(tl, "FOLLOWING", "RANKED_FOLLOWING");
            boolean feed = following || tlStarts(tl, "FOR_YOU", "HOME_SUBSCRIBED", "EXPLORE_FOR_YOU",
                    "LIST_POSTS", "COMMUNITIES_HOME", "COMMUNITY_DETAIL_POSTS", "TOPIC");

            if (val[DISABLE_HOME] && tlStarts(tl, HOME_TIMELINES)) return null;
            if (val[HIDE_ADS] && isAd(item)) return null;
            if (val[FOLLOWING_ONLY] && following && !followingKeep(item)) return null;
            if (val[HIDE_VIDEOS] && feed && item instanceof j1 && postHasMedia(((j1) item).a, y4.class)) return null;
            if (val[HIDE_PHOTOS] && feed && item instanceof j1 && postHasMedia(((j1) item).a, v4.class)) return null;

            if (item instanceof a1) {                      // module whose items were all filtered out
                List l = ((a1) item).a;
                if (l != null && l.isEmpty()) return null;
            }
            if (val[HIDE_ADS] && val[HIDE_PREROLL] && item instanceof j1) {
                j1 p = (j1) item;
                if (p.f != null) {                         // prerollMetadata (video pre-roll ad)
                    return new j1(p.a, p.b, p.c, p.d, p.e, null, p.g, p.h, p.i, p.j, p.k, p.l);
                }
            }
        } catch (Throwable t) {
            Log.w(TAG, "filterItem", t);
        }
        return item;
    }

    private static boolean isAd(Object item) {
        if (item instanceof p1) return true;                                   // RtbImageAd / SSP slot
        if (item instanceof j1) {
            j1 p = (j1) item;
            return p.e != null || isPromotedEntry(p.c);                        // promotedMetadata
        }
        if (item instanceof b2) {
            b2 u = (b2) item;
            return u.h != null || isPromotedEntry(u.c);
        }
        if (item instanceof y1) {
            y1 t = (y1) item;
            return (t.a != null && t.a.j != null) || isPromotedEntry(t.c);
        }
        if (item instanceof m0) {                                              // "promoted_hero"
            m0 e = (m0) item;
            return (e.a != null && e.a.i != null) || isPromotedEntry(e.c);
        }
        if (item instanceof a1) return isPromotedEntry(((a1) item).f);
        return false;
    }

    private static boolean isPromotedEntry(String entryId) {
        if (entryId == null) return false;
        return entryId.startsWith("promoted") || entryId.contains("-promoted-")
                || entryId.startsWith("superhero-");
    }

    // ---- "following only" ----

    private static boolean followingKeep(Object item) {
        if (item instanceof j0) return true;                 // cursors are needed for paging
        if (item instanceof j1) return followedPost(((j1) item).a);
        if (item instanceof a1) {                            // conversation modules: keep, "who to follow": drop
            List l = ((a1) item).a;
            if (l == null) return true;
            for (Object o : l) {
                if (o instanceof d1 && ((d1) o).a instanceof b2) return false;
            }
            return true;
        }
        return false;                                        // users, trends, prompts, topics, labels…
    }

    private static boolean followedPost(Object post) {
        Object author = null;
        boolean repost = false;
        if (post instanceof s1) {
            s1 s = (s1) post;
            if (s.b != null) author = s.b.f;
            repost = s.d != null;
        } else if (post instanceof v0) {
            author = ((v0) post).f;
        }
        if (repost && val[FOLLOWING_NO_REPOSTS] && !isOwn(author)) return false;
        if (!(author instanceof g5)) return true;            // unknown -> keep
        g5 u = (g5) author;
        if (isOwn(u)) return true;
        if (u.h == null || followFailOpen) return true;      // relationship unknown -> keep
        if (u.h.a) {
            followKept++;
            return true;
        }
        followDropped++;
        if (followKept == 0 && followDropped >= 15) {        // data looks wrong: never empty the whole feed
            followFailOpen = true;
            toast("X Mod: could not read who you follow, so the Following filter is paused");
            return true;
        }
        return false;
    }

    private static boolean isOwn(Object author) {
        return author instanceof g5 && ((g5) author).a != null && ownIds.contains(((g5) author).a.a);
    }

    // ---- videos ----

    private static boolean postHasMedia(Object post, Class<?> kind) {
        if (post instanceof v0) return mediaHas(((v0) post).d, kind);
        if (post instanceof s1) {
            s1 s = (s1) post;
            if (s.b != null && mediaHas(s.b.d, kind)) return true;
            if (s.d != null) {
                q8 r = s.d;
                if (r.a != null && mediaHas(r.a.d, kind)) return true;
                if (r.b != null && r.b != post && postHasMedia(r.b, kind)) return true;
            }
            return s.c != null && s.c != post && postHasMedia(s.c, kind);
        }
        return false;
    }

    /** kind: y4 = MediaContentVideo, v4 = MediaContentImage */
    private static boolean mediaHas(Object media, Class<?> kind) {
        if (!(media instanceof Collection)) return false;
        for (Object m : (Collection) media) if (kind.isInstance(m)) return true;
        return false;
    }

    /** video.tab.h2: when a video is opened from a post, show just that post (bounded playlist of one). */
    public static Object videoPlaylist(Object pinnedPost, Object playlist) {
        if (!val[SINGLE_VIDEO] || playlist != null || !(pinnedPost instanceof qt)) return playlist;
        try {
            i6 id = ((qt) pinnedPost).a();
            if (id == null) return null;
            return new gt(Collections.singletonList(id), id);
        } catch (Throwable t) {
            Log.w(TAG, "videoPlaylist", t);
            return null;
        }
    }

    /** Feature switch override. -1 = keep server value, 0 = false, 1 = true. */
    public static int fsBool(String key) {
        if (!val[HIDE_ADS] || key == null) return -1;
        if (key.equals("android_x_lite_ssp_ads_enabled")
                || key.equals("android_x_lite_ncaafb_hub_ads_enabled")) {
            return 0;
        }
        return -1;
    }

    // =================================================================== feed limit

    /** Called on every LoadMore request of a bottom pager. true = swallow the request (no more pages). */
    public static boolean blockBottomPaging(Object pagerObj) {
        if (!val[LIMIT_FEED]) return false;
        try {
            h pager = (h) pagerObj;
            Object type = pager.b.c();
            String name = type == null ? "" : ((Enum<?>) type).name();
            boolean home = name.equals("FOR_YOU") || name.equals("FOLLOWING")
                    || name.equals("RANKED_FOLLOWING") || name.equals("HOME_SUBSCRIBED");
            boolean other = name.equals("EXPLORE_FOR_YOU") || name.equals("VIDEO_TAB")
                    || name.equals("CONNECT_TAB");
            if (!home && !(val[LIMIT_OTHER] && other)) return false;
            int count = pager.n;
            if (count <= 0) return false;                  // never block the very first load
            int limit = LIMITS[limitIdx];
            if (limit > 0 && count < limit) return false;
            long now = SystemClock.uptimeMillis();
            if (now - lastEndToast > 15000) {
                lastEndToast = now;
                toast("End of feed: infinite scrolling is off (X Mod)");
            }
            return true;
        } catch (Throwable t) {
            Log.w(TAG, "blockBottomPaging", t);
            return false;
        }
    }

    private static void toast(final String s) {
        if (main == null || appCtx == null) return;
        main.post(() -> Toast.makeText(appCtx, s, Toast.LENGTH_SHORT).show());
    }

    // =================================================================== menu

    /** Called when the X logo on Home is tapped. true = consume (menu opened). */
    public static boolean onLogoClick() {
        long now = SystemClock.uptimeMillis();
        if (now - lastLogoTap < 500) {
            lastLogoTap = 0;
            final Activity a = topActivity.get();
            if (a != null && main != null) {
                main.post(() -> showMenu(a));
                return true;
            }
        }
        lastLogoTap = now;
        return false;
    }

    public static void showMenu(final Activity a) {
        try {
            if (a.isFinishing() || a.isDestroyed()) return;
            final Context c = themed(a);
            int pad = dp(c, 20);

            LinearLayout root = new LinearLayout(c);
            root.setOrientation(LinearLayout.VERTICAL);
            root.setPadding(pad, dp(c, 8), pad, dp(c, 8));

            final Switch[] sw = new Switch[KEYS.length];

            root.addView(header(c, "Ads"));
            root.addView(sw[HIDE_ADS] = settingSwitch(c, HIDE_ADS));
            root.addView(sw[HIDE_PREROLL] = settingSwitch(c, HIDE_PREROLL));

            root.addView(header(c, "Video"));
            root.addView(sw[HIDE_VIDEOS] = settingSwitch(c, HIDE_VIDEOS));
            root.addView(sw[SINGLE_VIDEO] = settingSwitch(c, SINGLE_VIDEO));
            root.addView(header(c, "Photos"));
            root.addView(sw[HIDE_PHOTOS] = settingSwitch(c, HIDE_PHOTOS));

            root.addView(header(c, "Feed"));
            root.addView(sw[DISABLE_HOME] = settingSwitch(c, DISABLE_HOME));
            root.addView(sw[LIMIT_FEED] = settingSwitch(c, LIMIT_FEED));
            final TextView limLabel = small(c, "Stop after:");
            limLabel.setPadding(0, dp(c, 8), 0, 0);
            root.addView(limLabel);
            final RadioGroup rg = new RadioGroup(c);
            for (int i = 0; i < LIMITS.length; i++) {
                RadioButton rb = new RadioButton(c);
                rb.setId(1000 + i);
                rb.setText(LIMIT_LABELS[i]);
                rg.addView(rb);
            }
            rg.check(1000 + limitIdx);
            root.addView(rg);
            root.addView(sw[LIMIT_OTHER] = settingSwitch(c, LIMIT_OTHER));

            root.addView(header(c, "Following"));
            root.addView(sw[FOLLOWING_ONLY] = settingSwitch(c, FOLLOWING_ONLY));
            root.addView(sw[FOLLOWING_NO_REPOSTS] = settingSwitch(c, FOLLOWING_NO_REPOSTS));

            root.addView(header(c, "Lock"));
            final Switch swLock = new Switch(c);
            styleSwitch(c, swLock, (lockMode ? "🔒 " : "") + "Permanent lock",
                    lockMode
                            ? "On. Enabled features stay on until you reinstall the app. "
                            + "Anything you turn on and save from now on is locked too."
                            : "Features that are on now can't be turned off until you reinstall the app "
                            + "(or clear its data). Features that are off stay changeable.",
                    lockMode);
            swLock.setEnabled(!lockMode);
            root.addView(swLock);

            // enable/disable dependent controls
            final Runnable sync = () -> {
                boolean lim = sw[LIMIT_FEED].isChecked();
                limLabel.setEnabled(lim);
                for (int i = 0; i < rg.getChildCount(); i++) {
                    boolean allowedByLock = !(locked[LIMIT_FEED] && lockedLimitIdx >= 0 && i > lockedLimitIdx);
                    rg.getChildAt(i).setEnabled(lim && allowedByLock);
                }
                if (!locked[LIMIT_OTHER]) sw[LIMIT_OTHER].setEnabled(lim);
                if (!locked[FOLLOWING_NO_REPOSTS]) sw[FOLLOWING_NO_REPOSTS].setEnabled(sw[FOLLOWING_ONLY].isChecked());
            };
            CompoundButton.OnCheckedChangeListener l = (b, on) -> sync.run();
            sw[LIMIT_FEED].setOnCheckedChangeListener(l);
            sw[FOLLOWING_ONLY].setOnCheckedChangeListener(l);
            sync.run();

            TextView note = small(c, "Feed changes apply after you refresh the feed (pull down). "
                    + "Turning off Google ads takes effect after an app restart.");
            note.setPadding(0, dp(c, 12), 0, 0);
            root.addView(note);

            ScrollView sv = new ScrollView(c);
            sv.addView(root);

            new AlertDialog.Builder(c)
                    .setTitle("X Mod")
                    .setView(sv)
                    .setPositiveButton("Save", (d, w) -> trySave(a, sw, rg, swLock, false))
                    .setNeutralButton("Save and restart", (d, w) -> trySave(a, sw, rg, swLock, true))
                    .setNegativeButton("Cancel", null)
                    .show();
        } catch (Throwable t) {
            Log.e(TAG, "showMenu", t);
        }
    }

    private static Switch settingSwitch(Context c, int i) {
        Switch s = new Switch(c);
        styleSwitch(c, s, (locked[i] ? "🔒 " : "") + TITLES[i], SUBS[i], val[i]);
        if (locked[i]) s.setEnabled(false);
        return s;
    }

    /** Validates the dialog state, asks for confirmation if something is about to be locked forever. */
    private static void trySave(final Activity a, Switch[] sw, RadioGroup rg, Switch swLock, final boolean restart) {
        final boolean[] nv = new boolean[KEYS.length];
        for (int i = 0; i < KEYS.length; i++) nv[i] = locked[i] || sw[i].isChecked();
        int idx = rg.getCheckedRadioButtonId() - 1000;
        if (idx < 0 || idx >= LIMITS.length) idx = 0;
        if (locked[LIMIT_FEED] && lockedLimitIdx >= 0 && idx > lockedLimitIdx) idx = lockedLimitIdx;
        final int newLimitIdx = idx;
        final boolean newLock = lockMode || swLock.isChecked();

        final List<String> toLock = new ArrayList<>();
        if (newLock) {
            for (int i = 0; i < KEYS.length; i++) if (nv[i] && !locked[i]) toLock.add("• " + TITLES[i]);
            if (nv[LIMIT_FEED] && (lockedLimitIdx < 0 || newLimitIdx < lockedLimitIdx)) {
                toLock.add("• Feed limit: stop no later than " + LIMIT_LABELS[newLimitIdx]);
            }
        }
        if (!newLock || toLock.isEmpty()) {
            commit(a, nv, newLimitIdx, newLock, restart);
            return;
        }
        StringBuilder msg = new StringBuilder("These settings will be locked PERMANENTLY. The only way to turn them off "
                + "is to reinstall the app (or clear its data):\n\n");
        for (String s : toLock) msg.append(s).append('\n');
        if (!lockMode) msg.append("\nFeatures that are off stay changeable.");
        new AlertDialog.Builder(themed(a))
                .setTitle("Permanent lock")
                .setMessage(msg.toString())
                .setPositiveButton("Lock", (d, w) -> commit(a, nv, newLimitIdx, true, restart))
                .setNegativeButton("Cancel", null)
                .show();
    }

    private static void commit(Activity a, boolean[] nv, int newLimitIdx, boolean newLock, boolean restart) {
        boolean adsChanged = nv[HIDE_ADS] != val[HIDE_ADS];
        System.arraycopy(nv, 0, val, 0, KEYS.length);
        limitIdx = newLimitIdx;
        lockMode = newLock;
        if (lockMode) {
            for (int i = 0; i < KEYS.length; i++) if (val[i]) locked[i] = true;
            if (val[LIMIT_FEED] && (lockedLimitIdx < 0 || limitIdx < lockedLimitIdx)) lockedLimitIdx = limitIdx;
        }
        followFailOpen = false;
        followKept = 0;
        followDropped = 0;
        save();
        if (restart) {
            restart(a);
        } else if (adsChanged) {
            Toast.makeText(a, "Restart the app to apply the ad settings", Toast.LENGTH_LONG).show();
        }
    }

    private static void restart(Activity a) {
        try {
            Intent i = a.getPackageManager().getLaunchIntentForPackage(a.getPackageName());
            if (i != null) {
                i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TASK);
                a.startActivity(i);
            }
            a.finishAffinity();
        } catch (Throwable ignored) {
        }
        main.postDelayed(() -> Process.killProcess(Process.myPid()), 300);
    }

    // =================================================================== ui helpers

    private static Context themed(Activity a) {
        boolean night = (a.getResources().getConfiguration().uiMode
                & Configuration.UI_MODE_NIGHT_MASK) == Configuration.UI_MODE_NIGHT_YES;
        return new ContextThemeWrapper(a, night ? android.R.style.Theme_DeviceDefault_Dialog_Alert
                : android.R.style.Theme_DeviceDefault_Light_Dialog_Alert);
    }

    private static int dp(Context c, int v) {
        return (int) TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v, c.getResources().getDisplayMetrics());
    }

    private static TextView header(Context c, String s) {
        TextView t = new TextView(c);
        t.setText(s);
        t.setTypeface(Typeface.DEFAULT_BOLD);
        t.setTextSize(TypedValue.COMPLEX_UNIT_SP, 15);
        t.setPadding(0, dp(c, 14), 0, dp(c, 4));
        return t;
    }

    private static TextView small(Context c, String s) {
        TextView t = new TextView(c);
        t.setText(s);
        t.setTextSize(TypedValue.COMPLEX_UNIT_SP, 12);
        t.setAlpha(0.7f);
        return t;
    }

    private static void styleSwitch(Context c, Switch s, String title, String sub, boolean checked) {
        s.setText(title + "\n" + sub);
        s.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
        s.setChecked(checked);
        s.setPadding(0, dp(c, 6), 0, dp(c, 6));
        s.setTextAlignment(View.TEXT_ALIGNMENT_VIEW_START);
    }
}
