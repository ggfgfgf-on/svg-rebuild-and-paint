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
static PyMethodDef methods[] = {
    {"pack_plan", pack_plan, METH_VARARGS, "pack plan dict to PSVB bytes"},
    {nullptr, nullptr, 0, nullptr}
};
static PyModuleDef mod = {PyModuleDef_HEAD_INIT, "plan_svg_ext", "fast plan packer", -1, methods};
PyMODINIT_FUNC PyInit_plan_svg_ext(void) { return PyModule_Create(&mod); }
