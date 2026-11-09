#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <cstdint>
#include <cstring>
#include <string>
#include <unordered_map>
#include <vector>

static void u16(std::vector<unsigned char>& v, uint16_t x) {
    v.push_back(x & 255); v.push_back((x >> 8) & 255);
}
static void u32(std::vector<unsigned char>& v, uint32_t x) {
    v.push_back(x & 255); v.push_back((x >> 8) & 255);
    v.push_back((x >> 16) & 255); v.push_back((x >> 24) & 255);
}
static void f32(std::vector<unsigned char>& v, float x) {
    unsigned char b[4]; std::memcpy(b, &x, 4); v.insert(v.end(), b, b + 4);
}
static void str16(std::vector<unsigned char>& v, const std::string& s) {
    u16(v, (uint16_t)s.size()); v.insert(v.end(), s.begin(), s.end());
}
static std::string pystr(PyObject* o) {
    if (!o || !PyUnicode_Check(o)) return "";
    const char* s = PyUnicode_AsUTF8(o);
    return s ? s : "";
}
static double pynum(PyObject* o, double d) {
    if (!o) return d;
    if (PyFloat_Check(o)) return PyFloat_AsDouble(o);
    if (PyLong_Check(o)) return (double)PyLong_AsLongLong(o);
    return d;
}
static PyObject* pack_plan(PyObject*, PyObject* args) {
    PyObject* doc = nullptr;
    if (!PyArg_ParseTuple(args, "O", &doc)) return nullptr;
    if (!PyDict_Check(doc)) { PyErr_SetString(PyExc_TypeError, "dict expected"); return nullptr; }
    PyObject* canvas = PyDict_GetItemString(doc, "canvas");
    PyObject* strokes = PyDict_GetItemString(doc, "strokes");
    if (!canvas || !PyList_Check(canvas) || PyList_Size(canvas) < 2 ||
        !strokes || !PyList_Check(strokes)) {
        PyErr_SetString(PyExc_ValueError, "canvas/strokes missing");
        return nullptr;
    }
    int W = (int)pynum(PyList_GetItem(canvas, 0), 0);
    int H = (int)pynum(PyList_GetItem(canvas, 1), 0);
    Py_ssize_t sc = PyList_Size(strokes);

    std::vector<std::string> order;
    std::unordered_map<std::string, std::string> blend;
    PyObject* layers = PyDict_GetItemString(doc, "layers");
    if (layers && PyList_Check(layers)) {
        Py_ssize_t lc = PyList_Size(layers);
        for (Py_ssize_t i = 0; i < lc; ++i) {
            PyObject* l = PyList_GetItem(layers, i);
            if (!l || !PyDict_Check(l)) continue;
            std::string n = pystr(PyDict_GetItemString(l, "name"));
            if (n.empty()) n = "layer";
            if (!blend.count(n)) { order.push_back(n); blend[n] = pystr(PyDict_GetItemString(l, "blend")); }
        }
    }
    for (Py_ssize_t i = 0; i < sc; ++i) {
        PyObject* s = PyList_GetItem(strokes, i);
        if (!s || !PyDict_Check(s)) continue;
        std::string n = pystr(PyDict_GetItemString(s, "layer"));
        if (n.empty()) n = "layer";
        if (!blend.count(n)) { order.push_back(n); blend[n] = "normal"; }
    }
    std::unordered_map<std::string, int> idx;
    for (size_t i = 0; i < order.size(); ++i) idx[order[i]] = (int)i;

    std::vector<unsigned char> v;
    v.reserve(2 * 1024 * 1024);
    v.insert(v.end(), {'P','S','V','B'}); u32(v, 1);
    u32(v, (uint32_t)W); u32(v, (uint32_t)H);
    u32(v, (uint32_t)order.size()); u32(v, (uint32_t)sc);
    for (const auto& n : order) { str16(v, blend[n]); str16(v, n); }

    for (Py_ssize_t i = 0; i < sc; ++i) {
        PyObject* s = PyList_GetItem(strokes, i);
        if (!s || !PyDict_Check(s)) continue;
        std::string n = pystr(PyDict_GetItemString(s, "layer"));
        if (n.empty()) n = "layer";
        u16(v, (uint16_t)idx[n]);
        str16(v, pystr(PyDict_GetItemString(s, "preset")));
        f32(v, (float)pynum(PyDict_GetItemString(s, "size"), 8.0));
        f32(v, (float)pynum(PyDict_GetItemString(s, "opacity"), 1.0));
        f32(v, (float)pynum(PyDict_GetItemString(s, "hardness"), 0.7));
        PyObject* c = PyDict_GetItemString(s, "color");
        double r = 217, g = 180, b = 90;
        if (c && PyList_Check(c) && PyList_Size(c) >= 3) {
            r = pynum(PyList_GetItem(c, 0), r);
            g = pynum(PyList_GetItem(c, 1), g);
            b = pynum(PyList_GetItem(c, 2), b);
        }
        f32(v, (float)r); f32(v, (float)g); f32(v, (float)b);
        PyObject* pts = PyDict_GetItemString(s, "points");
        Py_ssize_t pc = (pts && PyList_Check(pts)) ? PyList_Size(pts) : 0;
        u16(v, (uint16_t)pc);
        for (Py_ssize_t j = 0; j < pc; ++j) {
            PyObject* p = PyList_GetItem(pts, j);
            double x = 0, y = 0, pr = 1;
            if (p && PyList_Check(p)) {
                if (PyList_Size(p) > 0) x = pynum(PyList_GetItem(p, 0), 0);
                if (PyList_Size(p) > 1) y = pynum(PyList_GetItem(p, 1), 0);
                if (PyList_Size(p) > 2) pr = pynum(PyList_GetItem(p, 2), 1);
            }
            f32(v, (float)x); f32(v, (float)y); f32(v, (float)pr);
        }
        str16(v, pystr(PyDict_GetItemString(s, "tag")));
    }
    return PyBytes_FromStringAndSize((const char*)v.data(), (Py_ssize_t)v.size());
}

struct Rd {
    const unsigned char* p; Py_ssize_t n, i; bool ok;
    Rd(const unsigned char* d, Py_ssize_t len) : p(d), n(len), i(0), ok(true) {}
    bool need(Py_ssize_t k) { if (i + k > n) { ok = false; return false; } return true; }
    uint8_t u8() { if (!need(1)) return 0; return p[i++]; }
    uint16_t u16() { if (!need(2)) return 0; uint16_t v = (uint16_t)(p[i] | (p[i+1] << 8)); i += 2; return v; }
    uint32_t u32() { if (!need(4)) return 0; uint32_t v = (uint32_t)p[i] | ((uint32_t)p[i+1]<<8) | ((uint32_t)p[i+2]<<16) | ((uint32_t)p[i+3]<<24); i+=4; return v; }
    int32_t i32() { return (int32_t)u32(); }
    float f32() { uint32_t v = u32(); float f; std::memcpy(&f, &v, 4); return f; }
    std::string s16() { uint16_t k = u16(); if (!need(k)) return ""; std::string s((const char*)p+i, k); i += k; return s; }
};
static PyObject* mkfloat(double v) { return PyFloat_FromDouble(v); }
static PyObject* unpack_plan(PyObject*, PyObject* args) {
    Py_buffer view;
    if (!PyArg_ParseTuple(args, "y*", &view)) return nullptr;
    Rd r((const unsigned char*)view.buf, view.len);
    if (r.u32() != 0x42565350 || r.u32() != 1) {
        PyBuffer_Release(&view); PyErr_SetString(PyExc_ValueError, "bad PSVB"); return nullptr;
    }
    int W = r.i32(), H = r.i32(), lc = r.i32(), sc = r.i32();
    if (!r.ok || lc < 0 || sc < 0) { PyBuffer_Release(&view); PyErr_SetString(PyExc_ValueError, "bad PSVB header"); return nullptr; }
    std::vector<std::string> names(lc), blends(lc);
    for (int i = 0; i < lc; ++i) { blends[i] = r.s16(); names[i] = r.s16(); }
    PyObject* layers = PyList_New(lc);
    for (int i = 0; i < lc; ++i) {
        PyObject* l = PyDict_New();
        PyObject* n = PyUnicode_FromString(names[i].c_str());
        PyObject* b = PyUnicode_FromString(blends[i].c_str());
        PyDict_SetItemString(l, "name", n); PyDict_SetItemString(l, "blend", b);
        Py_DECREF(n); Py_DECREF(b); PyList_SET_ITEM(layers, i, l);
    }
    PyObject* strokes = PyList_New(sc);
    for (int k = 0; k < sc && r.ok; ++k) {
        int li = r.u16();
        std::string preset = r.s16();
        float size = r.f32(), op = r.f32(), hard = r.f32();
        float rr = r.f32(), gg = r.f32(), bb = r.f32();
        int pc = r.u16();
        PyObject* pts = PyList_New(pc);
        for (int j = 0; j < pc; ++j) {
            float x = r.f32(), y = r.f32(), pr = r.f32();
            PyObject* p3 = PyList_New(3);
            PyList_SET_ITEM(p3, 0, mkfloat(x)); PyList_SET_ITEM(p3, 1, mkfloat(y)); PyList_SET_ITEM(p3, 2, mkfloat(pr));
            PyList_SET_ITEM(pts, j, p3);
        }
        std::string tag = r.s16();
        PyObject* st = PyDict_New();
        const char* ln = (li >= 0 && li < lc) ? names[li].c_str() : "layer";
        PyObject* v;
        v = PyUnicode_FromString(ln); PyDict_SetItemString(st, "layer", v); Py_DECREF(v);
        v = PyUnicode_FromString(preset.c_str()); PyDict_SetItemString(st, "preset", v); Py_DECREF(v);
        v = mkfloat(size); PyDict_SetItemString(st, "size", v); Py_DECREF(v);
        v = mkfloat(op); PyDict_SetItemString(st, "opacity", v); Py_DECREF(v);
        v = mkfloat(hard); PyDict_SetItemString(st, "hardness", v); Py_DECREF(v);
        PyObject* col = PyList_New(3);
        PyList_SET_ITEM(col, 0, mkfloat(rr)); PyList_SET_ITEM(col, 1, mkfloat(gg)); PyList_SET_ITEM(col, 2, mkfloat(bb));
        PyDict_SetItemString(st, "color", col); Py_DECREF(col);
        PyDict_SetItemString(st, "points", pts); Py_DECREF(pts);
        if (!tag.empty()) { v = PyUnicode_FromString(tag.c_str()); PyDict_SetItemString(st, "tag", v); Py_DECREF(v); }
        PyList_SET_ITEM(strokes, k, st);
    }
    PyBuffer_Release(&view);
    if (!r.ok || r.i != r.n) {
        Py_DECREF(layers); Py_DECREF(strokes);
        PyErr_SetString(PyExc_ValueError, "truncated PSVB"); return nullptr;
    }
    PyObject* doc = PyDict_New();
    PyObject* canvas = PyList_New(2);
    PyList_SET_ITEM(canvas, 0, PyLong_FromLong(W)); PyList_SET_ITEM(canvas, 1, PyLong_FromLong(H));
    PyDict_SetItemString(doc, "canvas", canvas); Py_DECREF(canvas);
    PyDict_SetItemString(doc, "layers", layers); Py_DECREF(layers);
    PyDict_SetItemString(doc, "strokes", strokes); Py_DECREF(strokes);
    PyObject* cnt = PyLong_FromLong(sc); PyDict_SetItemString(doc, "count", cnt); Py_DECREF(cnt);
    return doc;
}

static PyMethodDef methods[] = {
    {"pack_plan", pack_plan, METH_VARARGS, "pack plan dict to PSVB bytes"},
    {"unpack_plan", unpack_plan, METH_VARARGS, "unpack PSVB bytes to plan dict"},
    {nullptr, nullptr, 0, nullptr}
};
static PyModuleDef mod = {PyModuleDef_HEAD_INIT, "plan_svg_ext", "fast plan packer", -1, methods};
PyMODINIT_FUNC PyInit_plan_svg_ext(void) { return PyModule_Create(&mod); }
