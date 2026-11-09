"""Edit compiled Android XML (AndroidManifest.xml) and resources.arsc string pools in place.

Supports what the clone build needs: renaming strings, appending strings, adding a
<uses-permission> element and replacing a resource-reference attribute with a literal string."""
import struct

RES_STRING_POOL = 0x0001
RES_TABLE = 0x0002
RES_XML = 0x0003
XML_RESOURCE_MAP = 0x0180
XML_START_NS = 0x0100
XML_START_ELEMENT = 0x0102
XML_END_ELEMENT = 0x0103
UTF8_FLAG = 0x100
TYPE_STRING = 0x03
ANDROID_NS = "http://schemas.android.com/apk/res/android"
ATTR_NAME, ATTR_LABEL = 0x01010003, 0x01010001


class StringPool:
    def __init__(self, data):
        (typ, hsz, size, count, scount, flags, sstart, ystart) = struct.unpack_from("<HHIIIIII", data, 0)
        assert typ == RES_STRING_POOL
        self.hsz, self.flags = hsz, flags
        self.utf8 = bool(flags & UTF8_FLAG)
        offs = struct.unpack_from(f"<{count}I", data, hsz)
        self.style_offs = list(struct.unpack_from(f"<{scount}I", data, hsz + 4 * count))
        self.raw, self.strings = [], []
        for o in offs:
            text, end = self._read(data, sstart + o)
            self.raw.append(bytes(data[sstart + o:end]))
            self.strings.append(text)
        self.orig = list(self.strings)
        self.offs = list(offs)
        self.blob = bytes(data[sstart:(ystart if scount else size)])
        self.style_data = data[ystart:size] if scount else b""

    def _read(self, d, p):
        """Returns (text, end offset including the terminator)."""
        if self.utf8:
            n, p = _u8len(d, p)          # UTF-16 length
            b, p = _u8len(d, p)          # byte length
            return d[p:p + b].decode("utf-8", "replace"), p + b + 1
        n = struct.unpack_from("<H", d, p)[0]
        p += 2
        if n & 0x8000:
            n = ((n & 0x7FFF) << 16) | struct.unpack_from("<H", d, p)[0]
            p += 2
        return d[p:p + 2 * n].decode("utf-16-le", "replace"), p + 2 * n + 2

    def index(self, s):
        return self.strings.index(s)

    def add(self, s):
        if s in self.strings:
            return self.strings.index(s)
        self.strings.append(s)
        return len(self.strings) - 1

    def _encode(self, s):
        if self.utf8:
            b = s.encode("utf-8")
            return _u8enc(len(s.encode("utf-16-le")) // 2) + _u8enc(len(b)) + b + b"\0"
        u = s.encode("utf-16-le")
        n = len(u) // 2
        head = struct.pack("<HH", 0x8000 | (n >> 16), n & 0xFFFF) if n > 0x7FFF else struct.pack("<H", n)
        return head + u + b"\0\0"

    def build(self):
        """Original string data is kept byte-for-byte; changed/added strings are appended."""
        blob, offs = bytearray(self.blob), []
        for i, s in enumerate(self.strings):
            if i < len(self.orig) and s == self.orig[i]:
                offs.append(self.offs[i])
                continue
            while len(blob) % 2:
                blob += b"\0"
            offs.append(len(blob))
            blob += self._encode(s)
        while len(blob) % 4:
            blob += b"\0"
        count, scount = len(self.strings), len(self.style_offs)
        sstart = self.hsz + 4 * count + 4 * scount
        ystart = sstart + len(blob) if scount else 0
        size = sstart + len(blob) + len(self.style_data)
        out = struct.pack("<HHIIIIII", RES_STRING_POOL, self.hsz, size, count, scount, self.flags, sstart, ystart)
        out += b"\0" * (self.hsz - 28)
        out += struct.pack(f"<{count}I", *offs) + struct.pack(f"<{scount}I", *self.style_offs)
        return out + bytes(blob) + self.style_data


def _u8len(d, p):
    n = d[p]
    p += 1
    if n & 0x80:
        n = ((n & 0x7F) << 8) | d[p]
        p += 1
    return n, p


def _u8enc(n):
    return bytes([0x80 | (n >> 8), n & 0xFF]) if n > 0x7F else bytes([n])


class AXml:
    """Compiled XML document: string pool + resource map + flat node list (kept as raw bytes)."""

    def __init__(self, data):
        typ, hsz, size = struct.unpack_from("<HHI", data, 0)
        assert typ == RES_XML, "not a compiled XML file"
        p = hsz
        self.pool, self.resmap, self.nodes = None, [], []
        while p < size:
            ctype, chsz, csize = struct.unpack_from("<HHI", data, p)
            chunk = data[p:p + csize]
            if ctype == RES_STRING_POOL:
                self.pool = StringPool(chunk)
            elif ctype == XML_RESOURCE_MAP:
                self.resmap = list(struct.unpack_from(f"<{(csize - chsz) // 4}I", chunk, chsz))
            else:
                self.nodes.append(bytearray(chunk))
            p += csize

    # -- queries ------------------------------------------------------------------
    def elements(self):
        """Yield (node_index, element_name, [(attr_offset, ns, name_idx, raw, type, data)])."""
        for i, n in enumerate(self.nodes):
            ctype, chsz = struct.unpack_from("<HH", n, 0)
            if ctype != XML_START_ELEMENT:
                continue
            ns, name, astart, asize, acount = struct.unpack_from("<iiHHH", n, chsz)
            attrs = []
            for k in range(acount):
                off = chsz + astart + k * asize
                ans, aname, raw = struct.unpack_from("<iii", n, off)
                _, _, dtype, data = struct.unpack_from("<HBBI", n, off + 12)
                attrs.append((off, ans, aname, raw, dtype, data))
            yield i, self.pool.strings[name], attrs

    def attr_res_id(self, name_idx):
        return self.resmap[name_idx] if 0 <= name_idx < len(self.resmap) else None

    def attr_name(self, name_idx):
        return self.pool.strings[name_idx]

    def string_attr(self, elem, attr):
        for _, name, attrs in self.elements():
            if name == elem:
                for a in attrs:
                    if self.attr_name(a[2]) == attr and a[3] >= 0:
                        return self.pool.strings[a[3]]
        return None

    # -- edits --------------------------------------------------------------------
    def set_attr_string(self, node_index, attr_off, value):
        idx = self.pool.add(value)
        struct.pack_into("<i", self.nodes[node_index], attr_off + 8, idx)
        struct.pack_into("<HBBI", self.nodes[node_index], attr_off + 12, 8, 0, TYPE_STRING, idx)

    def add_uses_permission(self, perm):
        for _, name, attrs in self.elements():
            if name == "uses-permission" and any(a[3] >= 0 and self.pool.strings[a[3]] == perm for a in attrs):
                return False
        ns = self.pool.index(ANDROID_NS)
        name_attr = self.resmap.index(ATTR_NAME)
        tag = self.pool.add("uses-permission")
        val = self.pool.add(perm)
        # insert before the <application> start element
        pos = next(i for i, n, _ in self.elements() if n == "application")
        start = struct.pack("<HHIii", XML_START_ELEMENT, 16, 36 + 20, 0, -1)
        start += struct.pack("<iiHHHHHH", -1, tag, 20, 20, 1, 0, 0, 0)
        start += struct.pack("<iii", ns, name_attr, val) + struct.pack("<HBBI", 8, 0, TYPE_STRING, val)
        end = struct.pack("<HHIii", XML_END_ELEMENT, 16, 24, 0, -1) + struct.pack("<ii", -1, tag)
        self.nodes[pos:pos] = [bytearray(start), bytearray(end)]
        return True

    def build(self):
        body = self.pool.build()
        if self.resmap:
            body += struct.pack("<HHI", XML_RESOURCE_MAP, 8, 8 + 4 * len(self.resmap))
            body += struct.pack(f"<{len(self.resmap)}I", *self.resmap)
        for n in self.nodes:
            body += bytes(n)
        return struct.pack("<HHI", RES_XML, 8, 8 + len(body)) + body


class ResTable:
    """resources.arsc: only the global string pool is editable here."""

    def __init__(self, data):
        typ, hsz, size, self.npkg = struct.unpack_from("<HHII", data, 0)
        assert typ == RES_TABLE
        p = hsz
        ctype, chsz, csize = struct.unpack_from("<HHI", data, p)
        assert ctype == RES_STRING_POOL
        self.pool = StringPool(data[p:p + csize])
        self.rest = data[p + csize:size]

    def build(self):
        pool = self.pool.build()
        return struct.pack("<HHII", RES_TABLE, 12, 12 + len(pool) + len(self.rest), self.npkg) + pool + self.rest


# ---------------------------------------------------------------------- clone helpers

def rename_package(doc, old, new, is_class):
    """Rename `old` (package) and `old.*` strings that are not class names (authorities, permissions...)."""
    changed = []
    for i, s in enumerate(doc.pool.strings):
        if s == old or (s.startswith(old + ".") and not is_class(s)):
            doc.pool.strings[i] = new + s[len(old):]
            changed.append((s, doc.pool.strings[i]))
    return changed


def set_app_label(doc, label):
    """Replace the <application> label (a resource reference) and every attribute that uses the
    same reference (launcher activity-aliases) with a literal string."""
    app_ref = None
    for i, name, attrs in doc.elements():
        if name == "application":
            for a in attrs:
                if doc.attr_res_id(a[2]) == ATTR_LABEL:
                    app_ref = a[5] if a[4] != TYPE_STRING else None
    n = 0
    for i, name, attrs in list(doc.elements()):
        for a in attrs:
            if doc.attr_res_id(a[2]) == ATTR_LABEL and (name == "application" or (app_ref is not None and a[5] == app_ref and a[4] != TYPE_STRING)):
                doc.set_attr_string(i, a[0], label)
                n += 1
    return n
