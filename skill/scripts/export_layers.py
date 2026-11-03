"""分层导出：Krita .kra（zip + maindoc.xml + 每层 PNG）与 Photoshop .psd（8BPS，raw 通道）。

只依赖标准库 + Pillow；图层数据来自 paint_core.dll 的 paint_layer_rgba。
"""
from __future__ import annotations
import io, struct, zipfile
from pathlib import Path
import numpy as np
from PIL import Image


def _png_bytes(rgba):
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, "PNG")
    return buf.getvalue()


def write_kra(path, layers, merged=None, name="painting"):
    """layers: [(layer_name, rgba_uint8) ...] 自下而上。"""
    path = Path(path)
    h, w = layers[0][1].shape[:2]
    merged = merged if merged is not None else layers[-1][1]
    nodes, files = [], []
    for i, (nm, rgba) in enumerate(layers):
        fn = "layer%d" % i
        files.append(("layers/%s" % fn, _png_bytes(rgba)))
        nodes.append(
            '  <layer name="%s" visible="1" opacity="255" composite-op="svg:src-over" '
            'nodetype="paintlayer" filename="%s" x="0" y="0" />' % (nm, fn))
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


def write_psd(path, layers, merged=None):
    """最小 8BPS 分层 PSD（RGB+A，raw 未压缩）。layers: [(name, rgba) ...] 自下而上。"""
    path = Path(path)
    h, w = layers[0][1].shape[:2]
    comp = merged if merged is not None else layers[-1][1]
    out = bytearray()
    out += b"8BPS" + struct.pack(">H", 1) + b"\0" * 6
    out += struct.pack(">HIIHH", 4, h, w, 8, 3)
    out += struct.pack(">I", 0)          # color mode data
    out += struct.pack(">I", 0)          # image resources

    records, chan_data = b"", b""
    for nm, rgba in layers:
        rec = struct.pack(">iiii", 0, 0, h, w) + struct.pack(">H", 4)
        for cid, plane in ((0, rgba[:, :, 0]), (1, rgba[:, :, 1]),
                           (2, rgba[:, :, 2]), (-1, rgba[:, :, 3])):
            raw = plane.tobytes()
            rec += struct.pack(">hI", cid, len(raw) + 2)
            chan_data += struct.pack(">H", 0) + raw
        rec += b"8BIM" + b"norm" + bytes([255, 0, 0, 0])   # blend, opacity, clipping, flags
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
