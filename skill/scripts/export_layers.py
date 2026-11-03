"""分层导出：Krita .kra（zip + maindoc.xml + 每层 PNG）与 Photoshop .psd（8BPS，raw 通道）。

只依赖标准库 + Pillow；图层数据来自 paint_core.dll 的 paint_layer_rgba。
"""
from __future__ import annotations
import io, struct, zipfile
from pathlib import Path
import numpy as np
from PIL import Image


KRA_BLEND = {
    "normal": "svg:src-over",
    "multiply": "svg:multiply",
    "screen": "svg:screen",
    "overlay": "svg:overlay",
    "softlight": "svg:soft-light",
    "hardlight": "svg:hard-light",
    "darken": "svg:darken",
    "lighten": "svg:lighten",
    "colordodge": "svg:color-dodge",
    "colorburn": "svg:color-burn",
    "difference": "svg:difference",
}

PSD_BLEND = {
    "normal": b"norm",
    "multiply": b"mul ",
    "screen": b"scrn",
    "overlay": b"over",
    "softlight": b"sftl",
    "hardlight": b"hLit",
    "darken": b"dark",
    "lighten": b"lght",
    "colordodge": b"div ",
    "colorburn": b"idiv",
    "difference": b"diff",
}


def _kra_blend(name):
    return KRA_BLEND.get(str(name or "normal").lower(), "svg:src-over")


def _psd_blend(name):
    key = PSD_BLEND.get(str(name or "normal").lower(), PSD_BLEND["normal"])
    return key if len(key) == 4 else key.ljust(4, b" ")


def blends_for(layers, blends):
    """把 blends（dict: 层名->blend 或 list 与 layers 并行）对齐到 layers。"""
    out = []
    for i, item in enumerate(layers):
        nm = item[0]
        if isinstance(blends, dict):
            out.append(blends.get(nm, "normal"))
        elif isinstance(blends, (list, tuple)) and i < len(blends):
            out.append(blends[i])
        else:
            out.append("normal")
    return out


def _png_bytes(rgba):
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, "PNG")
    return buf.getvalue()


def write_kra(path, layers, merged=None, name="painting", blends=None):
    """layers: [(layer_name, rgba_uint8) ...] 自下而上。

    blends: 层名->混合模式（或与 layers 并行的 list）。不传时全按 normal，
    但**不要**再写死 svg:src-over——那会让 Krita 预览和引擎渲染不是同一张。
    """
    path = Path(path)
    h, w = layers[0][1].shape[:2]
    merged = merged if merged is not None else layers[-1][1]
    bl = blends_for(layers, blends)
    nodes, files = [], []
    for i, (nm, rgba) in enumerate(layers):
        fn = "layer%d" % i
        files.append(("layers/%s" % fn, _png_bytes(rgba)))
        nodes.append(
            '  <layer name="%s" visible="1" opacity="255" composite-op="%s" '
            'nodetype="paintlayer" filename="%s" x="0" y="0" />' % (nm, _kra_blend(bl[i]), fn))
    maindoc = ("<?xml version='1.0' encoding='UTF-8'?>\n<!DOCTYPE DOC>\n"
               '<DOC xmlns="http://www.calligra.org/DTD/krita">\n'
               ' <IMAGE name="%s" width="%d" height="%d" colorspacename="RGBA" mime="image/x-krita">\n'
               '  <layers>\n%s\n  </layers>\n </IMAGE>\n</DOC>\n' % (name, w, h, "\n".join(nodes)))
    docinfo = ("<?xml version='1.0' encoding='UTF-8'?>\n<!DOCTYPE document-info>\n"
               '<document-info xmlns="http://www.calligra.org/DTD/document-info">\n'
               ' <about><abstract></abstract></about>\n</document-info>\n')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/x-krita", compress_type=zipfile.ZIP_STORED)
        z.writestr("maindoc.xml", maindoc)
        z.writestr("documentinfo.xml", docinfo)
        z.writestr("mergedimage.png", _png_bytes(merged))
        prev = np.asarray(Image.fromarray(merged, "RGBA").resize((w // 4, h // 4)))
        z.writestr("preview.png", _png_bytes(prev))
        for nm, data in files:
            z.writestr(nm, data)
    return path


def write_psd(path, layers, merged=None, blends=None):
    """最小 8BPS 分层 PSD（RGB+A，raw 未压缩）。layers: [(name, rgba) ...] 自下而上。

    blends: 同 write_kra；写进每层的 '8BIM' 混合键，而不是固定 'norm'。
    """
    path = Path(path)
    h, w = layers[0][1].shape[:2]
    comp = merged if merged is not None else layers[-1][1]
    bl = blends_for(layers, blends)
    out = bytearray()
    out += b"8BPS" + struct.pack(">H", 1) + b"\0" * 6
    out += struct.pack(">HIIHH", 4, h, w, 8, 3)
    out += struct.pack(">I", 0)          # color mode data
    out += struct.pack(">I", 0)          # image resources

    records, chan_data = b"", b""
    for li, (nm, rgba) in enumerate(layers):
        rec = struct.pack(">iiii", 0, 0, h, w) + struct.pack(">H", 4)
        for cid, plane in ((0, rgba[:, :, 0]), (1, rgba[:, :, 1]),
                           (2, rgba[:, :, 2]), (-1, rgba[:, :, 3])):
            raw = plane.tobytes()
            rec += struct.pack(">hI", cid, len(raw) + 2)
            chan_data += struct.pack(">H", 0) + raw
        rec += b"8BIM" + _psd_blend(bl[li]) + bytes([255, 0, 0, 0])   # blend, opacity, clipping, flags
        nbytes = nm.encode("utf-8")
        nm_p = bytes([len(nbytes)]) + nbytes
        if len(nm_p) % 4:
            nm_p += b"\0" * (4 - len(nm_p) % 4)
        extra = struct.pack(">II", 0, 0) + nm_p
        rec += struct.pack(">I", len(extra)) + extra
        records += rec
    layer_info = struct.pack(">h", -len(layers)) + records + chan_data
    lm = struct.pack(">I", len(layer_info)) + layer_info + struct.pack(">I", 0)
    out += struct.pack(">I", len(lm)) + lm
    out += struct.pack(">H", 0)          # raw image data
    for c in range(4):
        out += comp[:, :, c].tobytes()
    path = Path(path)
    path.write_bytes(bytes(out))
    return path
