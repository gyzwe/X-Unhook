"""Minimal smali tree helpers: fast string search + per-class parsing."""
import re
import subprocess
from pathlib import Path


class Method:
    def __init__(self, header, body, start_line):
        self.header = header            # ".method public final a(Lx;)V"
        self.body = body                # list of lines (without .method/.end method)
        self.start_line = start_line
        m = re.match(r"\.method\s+(.*?)(\S+)\((.*?)\)(\S+)$", header.strip())
        self.flags = m.group(1).split()
        self.name = m.group(2)
        self.params = split_types(m.group(3))
        self.ret = m.group(4)
        self.sig = f"{self.name}({m.group(3)}){self.ret}"

    @property
    def static(self):
        return "static" in self.flags

    def text(self):
        return "\n".join(self.body)

    def strings(self):
        return re.findall(r'const-string(?:/jumbo)? [vp]\d+, "((?:[^"\\]|\\.)*)"', self.text())


class SmaliClass:
    def __init__(self, path: Path):
        self.path = path
        self.lines = path.read_text(encoding="utf-8").split("\n")
        self.name = self.super = None
        self.flags = []
        self.interfaces = []
        self.fields = []                # (name, type, flags)
        self.methods = []
        cur = None
        for i, line in enumerate(self.lines):
            s = line.strip()
            if s.startswith(".class "):
                parts = s.split()
                self.name, self.flags = parts[-1], parts[1:-1]
            elif s.startswith(".super "):
                self.super = s.split()[1]
            elif s.startswith(".implements "):
                self.interfaces.append(s.split()[1])
            elif s.startswith(".field ") and cur is None:
                decl = s.split(" = ")[0].split()
                name, typ = decl[-1].split(":", 1)
                self.fields.append((name, typ, decl[1:-1]))
            elif s.startswith(".method "):
                cur = (s, [], i)
            elif s == ".end method" and cur:
                self.methods.append(Method(cur[0], cur[1], cur[2]))
                cur = None
            elif cur is not None:
                cur[1].append(line)

    @property
    def is_interface(self):
        return "interface" in self.flags

    @property
    def is_enum(self):
        return self.super == "Ljava/lang/Enum;"

    def instance_fields(self):
        return [(n, t) for n, t, f in self.fields if "static" not in f]

    def method(self, name=None, sig=None, pred=None):
        res = [m for m in self.methods
               if (name is None or m.name == name) and (sig is None or m.sig == sig) and (pred is None or pred(m))]
        return res

    def tostring_fields(self):
        """Map labels printed by a Kotlin data-class toString() to fields: {"entryId": ("c", "Ljava/lang/String;")}.

        Labels and field reads both appear in declaration order, so they are zipped in order
        (works for StringBuilder chains and for R8 helper calls that take strings first)."""
        ts = self.method("toString", pred=lambda m: m.ret == "Ljava/lang/String;" and not m.params)
        if not ts:
            return {}
        text = ts[0].text()
        labels = []
        for s in re.findall(r'const-string(?:/jumbo)? [vp]\d+, "((?:[^"\\]|\\.)*)"', text):
            for lab in re.findall(r"(?:^|[(,]\s*)([A-Za-z_][A-Za-z0-9_]*)=", s):
                labels.append(lab)
        # data-class toString prints properties in declaration order; R8 names fields a, b, c, ...
        # in declaration order, while dex stores them sorted -> restore R8 order.
        fields = [(n, t) for n, t, f in self.fields if "static" not in f and "synthetic" not in f]
        if all(r8_name_key(n) is not None for n, _ in fields):
            fields.sort(key=lambda f: r8_name_key(f[0]))
            if len(labels) == len(fields):
                return dict(zip(labels, fields))
        by_name = {n: (n, t) for n, t in fields}           # unobfuscated class: labels == field names
        if labels and all(l in by_name for l in labels):
            return {l: by_name[l] for l in labels}
        return {}


class SmaliTree:
    def __init__(self, root):
        self.root = Path(root)
        self.dirs = sorted(p for p in self.root.iterdir() if p.is_dir() and p.name.startswith("smali"))
        self._cache = {}

    # -- search -------------------------------------------------------------------
    def grep(self, needle, regex=False):
        """Files containing needle (fixed string by default)."""
        tool = "ugrep" if subprocess.run(["sh", "-c", "command -v ugrep"], capture_output=True).returncode == 0 else "grep"
        args = [tool, "-rl", "--include=*.smali", "-E" if regex else "-F", "-e", needle] + [str(d) for d in self.dirs]
        out = subprocess.run(args, capture_output=True, text=True).stdout
        return sorted(Path(p) for p in out.split("\n") if p)

    def grep_lines(self, needle, regex=False):
        tool = "grep"
        args = [tool, "-rn", "--include=*.smali", "-E" if regex else "-F", "-e", needle] + [str(d) for d in self.dirs]
        out = subprocess.run(args, capture_output=True, text=True).stdout
        res = []
        for line in out.split("\n"):
            if not line:
                continue
            path, ln, text = line.split(":", 2)
            res.append((Path(path), int(ln), text))
        return res

    # -- classes ------------------------------------------------------------------
    def path_of(self, desc):
        rel = desc[1:-1] + ".smali"
        for d in self.dirs:
            p = d / rel
            if p.exists():
                return p
        return None

    def cls(self, desc_or_path):
        p = desc_or_path if isinstance(desc_or_path, Path) else self.path_of(desc_or_path)
        if p is None:
            return None
        if p not in self._cache:
            self._cache[p] = SmaliClass(p)
        return self._cache[p]

    def by_tostring(self, prefix):
        """The class whose toString() starts with the string literal `prefix` (e.g. 'UrtTimelinePost(')."""
        hits = []
        for p in self.grep('"' + prefix):
            c = self.cls(p)
            ts = c.method("toString", pred=lambda m: m.ret == "Ljava/lang/String;" and not m.params)
            if ts and any(s.startswith(prefix) for s in ts[0].strings()):
                hits.append(c)
        return hits

    def by_string(self, s):
        return [self.cls(p) for p in self.grep('"' + s + '"')]


R8_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"


def r8_name_key(name):
    """Sort key reproducing R8's minified naming order (a..z, A..Z, aa, ab, ...); None if not minified."""
    if not name or len(name) > 3 or any(ch not in R8_ALPHABET for ch in name):
        return None
    return (len(name), [R8_ALPHABET.index(ch) for ch in name])


def split_types(s):
    res, i = [], 0
    while i < len(s):
        j = i
        while s[j] == "[":
            j += 1
        if s[j] == "L":
            j = s.index(";", j)
        res.append(s[i:j + 1])
        i = j + 1
    return res


def reg_count(types):
    return sum(2 if t in ("J", "D") else 1 for t in types)
