// Windows plan <-> SVG bridge: RapidJSON (plan JSON), pugixml (SVG).
#include "rapidjson/document.h"
#include "rapidjson/writer.h"
#include "rapidjson/stringbuffer.h"
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include "pugixml.hpp"
#include "lcms2.h"
#include "clipper2/clipper.h"
#include <windows.h>
#include <gdiplus.h>
#include <wincrypt.h>
#include <objidl.h>
#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <filesystem>
#include <sstream>
#include <wchar.h>
#include <fstream>
#include <filesystem>
#include <sstream>
#include <functional>
#include <map>
#include <string>
#include <vector>

#ifdef _WIN32
#define EXPORT extern "C" __declspec(dllexport)
#else
#define EXPORT extern "C"
#endif

using namespace rapidjson;

static std::string hash_text(const std::string& s) {
    unsigned long long h = 1469598103934665603ULL;
    for (unsigned char c : s) { h ^= c; h *= 1099511628211ULL; }
    char b[32]; std::snprintf(b, sizeof(b), "%016llx", h); return std::string(b);
}

static std::string fmt(double v) {
    char b[48]; std::snprintf(b, sizeof(b), "%.3f", v);
    std::string s(b);
    while (!s.empty() && s.back() == '0') s.pop_back();
    if (!s.empty() && s.back() == '.') s.pop_back();
    if (s.empty() || s == "-0") s = "0";
    return s;
}

static void esc_to(std::string& out, const std::string& s) {
    for (char c : s) {
        switch (c) {
            case '&': out += "&amp;"; break;
            case '<': out += "&lt;"; break;
            case '>': out += "&gt;"; break;
            case '"': out += "&quot;"; break;
            default: out += c;
        }
    }
}

static void jesc_to(std::string& out, const std::string& s) {
    for (unsigned char c : s) {
        switch (c) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default:
                if (c < 0x20) { char b[8]; std::snprintf(b, sizeof(b), "\\u%04x", c); out += b; }
                else out += (char)c;
        }
    }
}

static double get_num(const Value& v, const char* k, double d) {
    auto it = v.FindMember(k);
    if (it != v.MemberEnd() && it->value.IsNumber()) return it->value.GetDouble();
    return d;
}

static const Value* get_arr(const Value& v, const char* k) {
    auto it = v.FindMember(k);
    return (it != v.MemberEnd() && it->value.IsArray()) ? &it->value : nullptr;
}

static std::string get_str(const Value& v, const char* k, const std::string& d = "") {
    auto it = v.FindMember(k);
    return (it != v.MemberEnd() && it->value.IsString()) ? it->value.GetString() : d;
}

static std::string color_hex(const Value& c) {
    int r = 217, g = 180, b = 90;
    if (c.IsArray() && c.Size() >= 3) {
        r = (int)std::lround(std::max(0.0, std::min(255.0, c[0].GetDouble())));
        g = (int)std::lround(std::max(0.0, std::min(255.0, c[1].GetDouble())));
        b = (int)std::lround(std::max(0.0, std::min(255.0, c[2].GetDouble())));
    }
    char buf[16]; std::snprintf(buf, sizeof(buf), "#%02x%02x%02x", r, g, b);
    return buf;
}

static std::string color_text(const Value& c) {
    if (c.IsArray() && c.Size() >= 3)
        return fmt(c[0].GetDouble()) + "," + fmt(c[1].GetDouble()) + "," + fmt(c[2].GetDouble());
    return "217,180,90";
}

static std::string d_from(const Value& pts) {
    std::string d;
    bool first = true;
    if (!pts.IsArray()) return "M 0 0";
    for (const auto& p : pts.GetArray()) {
        if (!p.IsArray() || p.Size() < 2) continue;
        d += first ? "M " : " L ";
        d += fmt(p[0].GetDouble()) + " " + fmt(p[1].GetDouble());
        first = false;
    }
    return d.empty() ? "M 0 0" : d;
}

static std::string p_from(const Value& pts) {
    std::string s;
    if (!pts.IsArray()) return "1";
    for (const auto& p : pts.GetArray()) {
        if (!p.IsArray() || p.Size() < 2) continue;
        if (!s.empty()) s += ",";
        s += fmt(p.Size() > 2 ? p[2].GetDouble() : 1.0);
    }
    return s.empty() ? "1" : s;
}

static std::string svg_from_plan(const std::string& input) {
    Document d;
    d.Parse(input.c_str(), input.size());
    if (d.HasParseError() || !d.IsObject()) return "ERR: invalid plan json";
    int w = d["canvas"][0].GetInt(), h = d["canvas"][1].GetInt();
    std::vector<std::string> order;
    std::map<std::string, std::string> blends;
    auto add = [&](const std::string& n, const std::string& b) {
        if (blends.find(n) == blends.end()) { order.push_back(n); blends[n] = b.empty() ? "normal" : b; }
    };
    if (const Value* ls = get_arr(d, "layers"))
        for (const auto& l : ls->GetArray())
            add(get_str(l, "name", "layer"), get_str(l, "blend", "normal"));
    std::map<std::string, std::vector<const Value*>> by;
    if (const Value* ss = get_arr(d, "strokes"))
        for (const auto& s : ss->GetArray()) {
            std::string n = get_str(s, "layer", "layer");
            add(n, "normal");
            by[n].push_back(&s);
        }
    std::map<const Value*, std::string> paint_ids;
    std::map<const Value*, std::string> pattern_ids;
    std::string defs;
    int paint_no = 0;
    int pattern_no = 0;
    if (const Value* ss1 = get_arr(d, "strokes")) for (const auto& s : ss1->GetArray()) {
        auto pi = s.FindMember("pattern");
        if (pi == s.MemberEnd() || !pi->value.IsObject()) continue;
        const Value& p = pi->value;
        std::string id = "pt" + std::to_string(pattern_no++);
        pattern_ids[&s] = id;
        defs += "<pattern id=\"" + id + "\" width=\"" + get_str(p, "width", "32") + "\" height=\"" +
                get_str(p, "height", "32") + "\" patternUnits=\"" + get_str(p, "units", "userSpaceOnUse") + "\">";
        defs += "<image href=\"data:image/png;base64," + get_str(p, "data", "") + "\" width=\"" +
                get_str(p, "width", "32") + "\" height=\"" + get_str(p, "height", "32") + "\"/></pattern>";
    }
    if (const Value* ss0 = get_arr(d, "strokes")) for (const auto& s : ss0->GetArray()) {
        auto pi = s.FindMember("paint");
        if (pi == s.MemberEnd() || !pi->value.IsObject()) continue;
        const Value& p = pi->value;
        std::string id = "p" + std::to_string(paint_no++);
        paint_ids[&s] = id;
        std::string type = get_str(p, "type", "linear");
        std::string units = get_str(p, "units", "objectBoundingBox");
        defs += "<" + std::string(type == "radial" ? "radialGradient" : "linearGradient") + " id=\"" + id + "\" gradientUnits=\"" + units + "\"";
        if (type == "radial") {
            defs += " cx=\"" + get_str(p, "cx", "50%") + "\" cy=\"" + get_str(p, "cy", "50%") + "\" r=\"" + get_str(p, "r", "50%") + "\"";
        } else {
            defs += " x1=\"" + get_str(p, "x1", "0%") + "\" y1=\"" + get_str(p, "y1", "0%") + "\" x2=\"" + get_str(p, "x2", "100%") + "\" y2=\"" + get_str(p, "y2", "0%") + "\"";
        }
        auto src = p.FindMember("source");
        if (src != p.MemberEnd() && src->value.IsObject()) {
            auto g = src->value.FindMember("gradient");
            if (g != src->value.MemberEnd() && g->value.IsObject()) {
                auto at = g->value.FindMember("attrs");
                if (at != g->value.MemberEnd() && at->value.IsObject()) {
                    for (const char* key : {"gradientTransform", "spreadMethod", "fx", "fy"}) {
                        auto kv = at->value.FindMember(key);
                        if (kv != at->value.MemberEnd() && kv->value.IsString()) {
                            defs += " " + std::string(key) + "=\"" + std::string(kv->value.GetString()) + "\"";
                        }
                    }
                }
            }
        }
        defs += ">";
        auto stops = p.FindMember("stops");
        if (stops != p.MemberEnd() && stops->value.IsArray()) for (const auto& st : stops->value.GetArray()) {
            double off = st.IsArray() && st.Size() > 0 ? st[0].GetDouble() : 0.0;
            const Value& c = st[1];
            double aa = st.Size() > 2 ? st[2].GetDouble() : 1.0;
            defs += "<stop offset=\"" + fmt(off) + "\" stop-color=\"#" + color_hex(c).substr(1) + "\" stop-opacity=\"" + fmt(aa) + "\"/>";
        }
        defs += "</" + std::string(type == "radial" ? "radialGradient" : "linearGradient") + ">";
    }
    std::string out;
    out.reserve(16 * 1024 * 1024);
    out += "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n";
    out += "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"" + std::to_string(w) +
           "\" height=\"" + std::to_string(h) + "\" viewBox=\"0 0 " +
           std::to_string(w) + " " + std::to_string(h) +
           "\" data-bridge=\"plan_svg\" shape-rendering=\"geometricPrecision\">\n";
    if (!defs.empty()) out += "<defs>" + defs + "</defs>\n";
    for (size_t li = 0; li < order.size(); ++li) {
        const std::string& name = order[li];
        const std::string& bl = blends[name];
        out += "<g id=\"L" + std::to_string(li) + "\" data-name=\"";
        esc_to(out, name);
        out += "\" data-blend=\""; esc_to(out, bl);
        out += "\" style=\"isolation:isolate;mix-blend-mode:"; esc_to(out, bl); out += "\">\n";
        auto it = by.find(name);
        if (it != by.end()) for (const Value* sp : it->second) {
            const Value& s = *sp;
            if (get_str(s, "preset", "") == "text") {
                out += "<text x=\"" + fmt(get_num(s, "x", 0.0)) + "\" y=\"" + fmt(get_num(s, "y", 0.0)) + "\"";
                out += " font-family=\""; esc_to(out, get_str(s, "font_family", "Arial"));
                out += "\" font-size=\"" + fmt(get_num(s, "font_size", 16.0)) + "\"";
                auto ci = s.FindMember("color");
                if (ci != s.MemberEnd() && ci->value.IsArray()) out += " fill=\"" + color_hex(ci->value) + "\"";
                out += " fill-opacity=\"" + fmt(get_num(s, "opacity", 1.0)) + "\"";
                out += " text-anchor=\""; esc_to(out, get_str(s, "text_anchor", "start")); out += "\"";
                auto tr = s.FindMember("transform");
                if (tr != s.MemberEnd() && tr->value.IsArray() && tr->value.Size() == 6) {
                    out += " transform=\"matrix(";
                    for (int k = 0; k < 6; ++k) { if (k) out += " "; out += fmt(tr->value[k].GetDouble()); }
                    out += ")\"";
                }
                out += ">";
                auto rr = s.FindMember("runs");
                if (rr != s.MemberEnd() && rr->value.IsArray()) {
                    for (const auto& r : rr->value.GetArray()) {
                        out += "<tspan fill=\"";
                        auto rc = r.FindMember("color");
                        if (rc != r.MemberEnd() && rc->value.IsArray()) esc_to(out, color_hex(rc->value)); else out += "#000";
                        out += "\" font-size=\"" + fmt(get_num(r, "font_size", 16.0)) + "\">";
                        esc_to(out, get_str(r, "text", ""));
                        out += "</tspan>";
                    }
                } else {
                    esc_to(out, get_str(s, "text", ""));
                }
                out += "</text>\n";
                continue;
            }
            if (get_str(s, "preset", "") == "image") {
                auto ii = s.FindMember("image");
                if (ii != s.MemberEnd() && ii->value.IsObject()) {
                    const Value& im = ii->value;
                    std::string data = get_str(im, "data", "");
                    std::string mime = get_str(im, "mime", "image/png");
                    out += "<image href=\"data:"; esc_to(out, mime); out += ";base64,"; esc_to(out, data); out += "\"";
                    out += " x=\"" + fmt(get_num(s, "x", 0.0)) + "\" y=\"" + fmt(get_num(s, "y", 0.0));
                    out += "\" width=\"" + fmt(get_num(s, "width", 1.0)) + "\" height=\"" + fmt(get_num(s, "height", 1.0));
                    out += "\" opacity=\"" + fmt(get_num(s, "opacity", 1.0)) + "\"";
                    auto tr = s.FindMember("transform");
                    if (tr != s.MemberEnd() && tr->value.IsArray() && tr->value.Size() == 6) {
                        out += " transform=\"matrix(";
                        for (int k = 0; k < 6; ++k) { if (k) out += " "; out += fmt(tr->value[k].GetDouble()); }
                        out += ")\"";
                    }
                    out += "/>\n";
                }
                continue;
            }
            const Value* pts = get_arr(s, "points");
            if (!pts || pts->Empty()) continue;
            double size = get_num(s, "size", 8.0);
            double op = get_num(s, "opacity", 1.0);
            double hard = get_num(s, "hardness", 0.7);
            std::string preset = get_str(s, "preset", "round_soft");
            std::string cap = (preset == "flat" || preset == "bristle") ? "butt" : "round";
            auto ci = s.FindMember("color");
            Value dummy(kArrayType); dummy.PushBack(217, d.GetAllocator()).PushBack(180, d.GetAllocator()).PushBack(90, d.GetAllocator());
            const Value& col = (ci != s.MemberEnd() && ci->value.IsArray()) ? ci->value : dummy;
            if (preset == "fill") {
                out += "<path d=\""; out += d_from(*pts); out += " Z";
                auto hh = s.FindMember("holes");
                if (hh != s.MemberEnd() && hh->value.IsArray()) {
                    for (const auto& h : hh->value.GetArray()) {
                        if (h.IsArray() && h.Size() >= 3) { out += " "; out += d_from(h); out += " Z"; }
                    }
                }
                out += "\" fill=\"";
                auto ptf = pattern_ids.find(&s);
                auto pfill = paint_ids.find(&s);
                if (ptf != pattern_ids.end()) out += "url(#" + ptf->second + ")";
                else if (pfill != paint_ids.end()) out += "url(#" + pfill->second + ")"; else out += color_hex(col);
                out += "\" fill-opacity=\""; out += fmt(op); out += "\" fill-rule=\"";
                esc_to(out, get_str(s, "fill_rule", "nonzero"));
                out += "\" data-layer=\""; esc_to(out, name);
                out += "\" data-preset=\"fill\" data-color=\""; out += color_text(col); out += "\"";
                auto ft = s.FindMember("tag"); if (ft != s.MemberEnd() && ft->value.IsString()) { out += " data-tag=\""; esc_to(out, ft->value.GetString()); out += "\""; }
                out += "/>\n"; continue;
            }
            out += "<path d=\""; out += d_from(*pts);
            out += "\" fill=\"none\" stroke=\"";
            auto pstroke = paint_ids.find(&s);
            if (pstroke != paint_ids.end()) out += "url(#" + pstroke->second + ")"; else out += color_hex(col);
            out += "\" stroke-width=\""; out += fmt(size);
            out += "\" stroke-opacity=\""; out += fmt(op);
            out += "\" stroke-linecap=\""; out += cap;
            out += "\" stroke-linejoin=\"round\" data-layer=\"";
            esc_to(out, name); out += "\" data-preset=\""; esc_to(out, preset);
            out += "\" data-size=\""; out += fmt(size);
            out += "\" data-opacity=\""; out += fmt(op);
            out += "\" data-hardness=\""; out += fmt(hard);
            out += "\" data-color=\""; out += color_text(col);
            out += "\" data-p=\""; out += p_from(*pts); out += "\"";
            auto ti = s.FindMember("tag");
            if (ti != s.MemberEnd() && ti->value.IsString()) {
                out += " data-tag=\""; esc_to(out, ti->value.GetString()); out += "\"";
            }
            out += "/>\n";
        }
        out += "</g>\n";
    }
    out += "</svg>\n";
    return out;
}

// ---------- SVG path parser ----------
static bool is_cmd(char c) {
    return c=='M'||c=='m'||c=='L'||c=='l'||c=='H'||c=='h'||c=='V'||c=='v'||
           c=='C'||c=='c'||c=='S'||c=='s'||c=='Q'||c=='q'||c=='T'||c=='t'||c=='A'||c=='a'||c=='Z'||c=='z';
}
struct PP {
    const char* p; const char* e;
    PP(const std::string& s) : p(s.c_str()), e(s.c_str()+s.size()) {}
    void ws() { while (p < e && (std::isspace((unsigned char)*p) || *p == ',')) ++p; }
    bool cmd(char& c) { ws(); if (p < e && is_cmd(*p)) { c = *p++; return true; } return false; }
    bool num() { ws(); return p < e && (std::isdigit((unsigned char)*p) || *p=='-' || *p=='+' || *p=='.'); }
    double n() { ws(); char* q=nullptr; double v=std::strtod(p,&q); if(q==p) return 0; p=q; return v; }
};
static void addpt(std::vector<double>& xs, std::vector<double>& ys, double x, double y) {
    if (!xs.empty() && std::fabs(xs.back()-x)<1e-9 && std::fabs(ys.back()-y)<1e-9) return;
    xs.push_back(x); ys.push_back(y);
}

static std::vector<double> fast_nums(const std::string& s) {
    std::vector<double> v; const char* p=s.c_str();
    while(*p){
        if(*p=='M'||*p=='L'||*p=='m'||*p=='l'||*p==' '||*p==','||*p=='\t'||*p=='\r'||*p=='\n'){ ++p; continue; }
        char* q=nullptr; double x=std::strtod(p,&q);
        if(q==p){ ++p; continue; }
        v.push_back(x); p=q;
    }
    return v;
}

static void parse_d(const std::string& d, std::vector<double>& xs, std::vector<double>& ys) {
    if (!d.empty() && d[0] == 'M' && d.find_first_of("CcSsQqTtAaHhVvZz") == std::string::npos) {
        auto v = fast_nums(d);
        for (size_t i = 0; i + 1 < v.size(); i += 2) { xs.push_back(v[i]); ys.push_back(v[i+1]); }
        return;
    }
    PP q(d); char c=0, prev=0;
    double cx=0,cy=0,sx=0,sy=0,px=0,py=0;
    while (true) {
        q.ws(); if (q.p>=q.e) break;
        char nc;
        if (q.cmd(nc)) c=nc;
        else if (!q.num()) break;
        bool rel = (c>='a' && c<='z');
        char C = (char)std::toupper((unsigned char)c);
        if (C=='Z') { cx=sx; cy=sy; addpt(xs,ys,cx,cy); prev=C; continue; }
        if (C=='M') {
            double x=q.n(), y=q.n(); if(rel){x+=cx;y+=cy;}
            cx=x; cy=y; sx=x; sy=y; addpt(xs,ys,cx,cy); c=rel?'l':'L'; prev=C; continue;
        }
        if (C=='L') {
            double x=q.n(), y=q.n(); if(rel){x+=cx;y+=cy;}
            cx=x; cy=y; addpt(xs,ys,cx,cy); prev=C;
        } else if (C=='H') { double x=q.n(); if(rel)x+=cx; cx=x; addpt(xs,ys,cx,cy); prev=C; }
        else if (C=='V') { double y=q.n(); if(rel)y+=cy; cy=y; addpt(xs,ys,cx,cy); prev=C; }
        else if (C=='C') {
            double x1=q.n(),y1=q.n(),x2=q.n(),y2=q.n(),x=q.n(),y=q.n();
            if(rel){x1+=cx;y1+=cy;x2+=cx;y2+=cy;x+=cx;y+=cy;}
            for(int k=1;k<=8;++k){double t=k/8.0,u=1-t;addpt(xs,ys,u*u*u*cx+3*u*u*t*x1+3*u*t*t*x2+t*t*t*x,u*u*u*cy+3*u*u*t*y1+3*u*t*t*y2+t*t*t*y);}
            px=x2;py=y2;cx=x;cy=y;prev=C;
        } else if (C=='S') {
            double x2=q.n(),y2=q.n(),x=q.n(),y=q.n(); if(rel){x2+=cx;y2+=cy;x+=cx;y+=cy;}
            double x1=(prev=='C'||prev=='S')?2*cx-px:cx, y1=(prev=='C'||prev=='S')?2*cy-py:cy;
            for(int k=1;k<=8;++k){double t=k/8.0,u=1-t;addpt(xs,ys,u*u*u*cx+3*u*u*t*x1+3*u*t*t*x2+t*t*t*x,u*u*u*cy+3*u*u*t*y1+3*u*t*t*y2+t*t*t*y);}
            px=x2;py=y2;cx=x;cy=y;prev=C;
        } else if (C=='Q') {
            double x1=q.n(),y1=q.n(),x=q.n(),y=q.n(); if(rel){x1+=cx;y1+=cy;x+=cx;y+=cy;}
            for(int k=1;k<=8;++k){double t=k/8.0,u=1-t;addpt(xs,ys,u*u*cx+2*u*t*x1+t*t*x,u*u*cy+2*u*t*y1+t*t*y);}
            px=x1;py=y1;cx=x;cy=y;prev=C;
        } else if (C=='T') {
            double x=q.n(),y=q.n(); if(rel){x+=cx;y+=cy;}
            double x1=(prev=='Q'||prev=='T')?2*cx-px:cx, y1=(prev=='Q'||prev=='T')?2*cy-py:cy;
            for(int k=1;k<=8;++k){double t=k/8.0,u=1-t;addpt(xs,ys,u*u*cx+2*u*t*x1+t*t*x,u*u*cy+2*u*t*y1+t*t*y);}
            px=x1;py=y1;cx=x;cy=y;prev=C;
        } else if (C=='A') {
            double rx=q.n(), ry=q.n(), rot=q.n(), laf=q.n(), sf=q.n(), x=q.n(), y=q.n();
            if(rel){x+=cx;y+=cy;}
            rx=std::fabs(rx); ry=std::fabs(ry);
            double phi=rot*3.14159265358979323846/180.0, cp=std::cos(phi), sp=std::sin(phi);
            double dx=(cx-x)/2.0, dy=(cy-y)/2.0;
            double x1=cp*dx+sp*dy, y1=-sp*dx+cp*dy;
            if(rx>1e-9 && ry>1e-9){
                double lam=(x1*x1)/(rx*rx)+(y1*y1)/(ry*ry);
                if(lam>1){double z=std::sqrt(lam);rx*=z;ry*=z;}
                double num=std::max(0.0,rx*rx*ry*ry-rx*rx*y1*y1-ry*ry*x1*x1);
                double den=rx*rx*y1*y1+ry*ry*x1*x1;
                double co=den>0?std::sqrt(num/den):0.0; if((laf!=0)==(sf!=0))co=-co;
                double cxp=co*rx*y1/ry, cyp=-co*ry*x1/rx;
                double ccx=cp*cxp-sp*cyp+(cx+x)/2.0, ccy=sp*cxp+cp*cyp+(cy+y)/2.0;
                auto ang=[](double ux,double uy,double vx,double vy){return std::atan2(ux*vy-uy*vx,ux*vx+uy*vy);};
                double t1=ang(1,0,(x1-cxp)/rx,(y1-cyp)/ry);
                double dt=ang((x1-cxp)/rx,(y1-cyp)/ry,(-x1-cxp)/rx,(-y1-cyp)/ry);
                if(sf==0 && dt>0)dt-=2*3.14159265358979323846;
                else if(sf!=0 && dt<0)dt+=2*3.14159265358979323846;
                for(int k=1;k<=16;++k){double t=t1+dt*k/16.0;addpt(xs,ys,ccx+rx*cp*std::cos(t)-ry*sp*std::sin(t),ccy+rx*sp*std::cos(t)+ry*cp*std::sin(t));}
            } else addpt(xs,ys,x,y);
            cx=x;cy=y;prev=C;
        } else break;
    }
}


static std::vector<double> nums(const std::string& s);

static std::vector<std::string> split_d_subpaths(const std::string& d) {
    std::vector<std::string> out; size_t start=std::string::npos;
    for (size_t i=0;i<d.size();++i) {
        if (d[i]=='M'||d[i]=='m') { if(start!=std::string::npos) out.push_back(d.substr(start,i-start)); start=i; }
    }
    if(start!=std::string::npos) out.push_back(d.substr(start));
    if(out.empty()) out.push_back(d);
    return out;
}

static void parse_center(const std::string& s, std::vector<double>& xs, std::vector<double>& ys, std::vector<double>& ps) {
    size_t i = 0;
    while (i < s.size()) {
        size_t j = s.find(';', i); if (j == std::string::npos) j = s.size();
        auto v = nums(s.substr(i, j - i));
        if (v.size() >= 2) { xs.push_back(v[0]); ys.push_back(v[1]); ps.push_back(v.size() >= 3 ? v[2] : 1.0); }
        i = j + 1;
    }
}

struct Mat { double a=1,b=0,c=0,d=1,e=0,f=0; };
static Mat mat_mul(const Mat& m, const Mat& n) {
    return Mat{m.a*n.a+m.c*n.b, m.b*n.a+m.d*n.b, m.a*n.c+m.c*n.d, m.b*n.c+m.d*n.d,
               m.a*n.e+m.c*n.f+m.e, m.b*n.e+m.d*n.f+m.f};
}
static Mat parse_transform(const std::string& s) {
    Mat m;
    size_t p=0;
    while ((p=s.find('(',p)) != std::string::npos) {
        size_t q=s.rfind(' ',p); size_t name0=(q==std::string::npos)?0:q+1;
        std::string name=s.substr(name0,p-name0);
        size_t e=s.find(')',p); if(e==std::string::npos)break;
        auto v=nums(s.substr(p+1,e-p-1));
        Mat t;
        if(name=="translate" && v.size()>=1){t.e=v[0];t.f=v.size()>1?v[1]:0;}
        else if(name=="scale" && v.size()>=1){t.a=v[0];t.d=v.size()>1?v[1]:v[0];}
        else if(name=="matrix" && v.size()>=6){t=Mat{v[0],v[1],v[2],v[3],v[4],v[5]};}
        else if(name=="rotate" && v.size()>=1){
            double a=v[0]*3.14159265358979323846/180.0, ca=std::cos(a), sa=std::sin(a);
            t=Mat{ca,sa,-sa,ca,0,0};
            if(v.size()>=3){ Mat tr{1,0,0,1,v[1],v[2]}, ti{1,0,0,1,-v[1],-v[2]}; t=mat_mul(mat_mul(tr,t),ti); }
        }
        m=mat_mul(m,t); p=e+1;
    }
    return m;
}
static void mat_apply(const Mat& m, double& x, double& y) { double nx=m.a*x+m.c*y+m.e, ny=m.b*x+m.d*y+m.f; x=nx; y=ny; }

static std::vector<double> nums(const std::string& s) {
    std::vector<double> v; const char* p=s.c_str();
    while(*p){ while(*p && (std::isspace((unsigned char)*p)||*p==','))++p; if(!*p)break; char*q=nullptr; double x=std::strtod(p,&q); if(q==p)break; v.push_back(x); p=q; }
    return v;
}
static std::map<std::string, std::vector<unsigned char>> g_icc;
static std::string g_base_dir;

static std::vector<double> icc_to_srgb(int r, int g, int b, const std::string& name) {
    auto it = g_icc.find(name);
    if (it == g_icc.end()) return {217,180,90};
    cmsHPROFILE src = cmsOpenProfileFromMem(it->second.data(), (cmsUInt32Number)it->second.size());
    cmsHPROFILE dst = cmsCreate_sRGBProfile();
    if (!src || !dst) { if(src)cmsCloseProfile(src); if(dst)cmsCloseProfile(dst); return {217,180,90}; }
    cmsHTRANSFORM tr = cmsCreateTransform(src, TYPE_RGB_8, dst, TYPE_RGB_8, INTENT_PERCEPTUAL, 0);
    unsigned char in[3] = {(unsigned char)r,(unsigned char)g,(unsigned char)b}, out[3] = {0,0,0};
    if (tr) cmsDoTransform(tr, in, out, 1);
    if (tr) cmsDeleteTransform(tr);
    cmsCloseProfile(src); cmsCloseProfile(dst);
    return {(double)out[0],(double)out[1],(double)out[2]};
}

static void load_icc_profiles(pugi::xml_node root) {
    g_icc.clear();
    for (pugi::xml_node ch=root.first_child(); ch; ch=ch.next_sibling()) {
        if (std::string(ch.name())!="color-profile") continue;
        std::string name=ch.attribute("name").as_string();
        std::string href=ch.attribute("href").as_string();
        if (href.empty()) href=ch.attribute("{http://www.w3.org/1999/xlink}href").as_string();
        if (href.empty()) href=ch.attribute("xlink:href").as_string();
        if (name.empty() || href.empty() || g_base_dir.empty()) continue;
        std::string path=href;
        if (path.size() && path[0]!='/' && path[1]!=':') path=g_base_dir+"/"+path;
        std::error_code ec;
        auto base = std::filesystem::weakly_canonical(std::filesystem::path(g_base_dir), ec);
        auto full = std::filesystem::weakly_canonical(std::filesystem::path(path), ec);
        if (ec || base.empty() || full.empty()) continue;
        auto bs = base.generic_string(), fs = full.generic_string();
        if (fs.rfind(bs, 0) != 0 || (fs.size() > bs.size() && fs[bs.size()] != '/')) continue;
        std::ifstream f(path, std::ios::binary);
        if (!f) continue;
        std::vector<unsigned char> data((std::istreambuf_iterator<char>(f)), std::istreambuf_iterator<char>());
        if (!data.empty()) g_icc[name]=std::move(data);
    }
}

static double srgb_gamma(double v){ v=std::max(0.0,std::min(1.0,v)); return v<=0.0031308?12.92*v:1.055*std::pow(v,1.0/2.4)-0.055; }
static std::vector<double> xyz2srgb(double x,double y,double z){ double r=3.2406*x-1.5372*y-0.4986*z,g=-0.9689*x+1.8758*y+0.0415*z,b=0.0557*x-0.2040*y+1.0570*z; return {(double)std::lround(srgb_gamma(r)*255),(double)std::lround(srgb_gamma(g)*255),(double)std::lround(srgb_gamma(b)*255)}; }
static std::vector<double> p3_color(double r,double g,double b){ auto lin=[](double v){return v<=0.04045?v/12.92:std::pow((v+0.055)/1.055,2.4);}; r=lin(r);g=lin(g);b=lin(b); double x=0.4865709*r+0.2656677*g+0.1982173*b,y=0.2289746*r+0.6917385*g+0.0792869*b,z=0.0*r+0.0451134*g+1.0439444*b; return xyz2srgb(x,y,z); }
static std::vector<double> lab_color(double L,double a,double bb){ double fy=(L+16)/116,fx=fy+a/500,fz=fy-bb/200; auto f=[](double t){return t>6.0/29?t*t*t:3*(6.0/29)*(6.0/29)*(t-4.0/29);}; return xyz2srgb(0.95047*f(fx),1.0*f(fy),1.08883*f(fz)); }
static std::vector<double> oklab_color(double L,double a,double b){ double l_=L+0.3963377774*a+0.2158037573*b,m_=L-0.1055613458*a-0.0638541728*b,s_=L-0.0894841775*a-1.2914855480*b; double l=l_*l_*l_,m=m_*m_*m_,s=s_*s_*s_; double r=4.0767416621*l-3.3077115913*m+0.2309699292*s,g=-1.2684380046*l+2.6097574011*m-0.3413193965*s,bb=-0.0041960863*l-0.7034186147*m+1.7076147010*s; return {(double)std::lround(srgb_gamma(r)*255),(double)std::lround(srgb_gamma(g)*255),(double)std::lround(srgb_gamma(bb)*255)}; }

static std::vector<double> color_from(const std::string& s) {
    if (s.rfind("icc-color(",0)==0) {
        size_t e=s.find(')');
        std::string body=s.substr(10, e==std::string::npos?std::string::npos:e-10);
        size_t c=body.find(',');
        if (c!=std::string::npos) {
            std::string name=body.substr(0,c);
            while(!name.empty() && std::isspace((unsigned char)name.front())) name.erase(name.begin());
            while(!name.empty() && std::isspace((unsigned char)name.back())) name.pop_back();
            auto v=nums(body.substr(c+1));
            if (v.size()>=3) return icc_to_srgb((int)v[0],(int)v[1],(int)v[2],name);
        }
        return {217,180,90};
    }
    if ((s.size()==7 || s.size()==4) && s[0]=='#') {
        auto h=[](char c)->int{ if(c>='0'&&c<='9')return c-'0'; if(c>='a'&&c<='f')return c-'a'+10; if(c>='A'&&c<='F')return c-'A'+10; return 0; };
        if (s.size()==4) return {double(h(s[1])*17),double(h(s[2])*17),double(h(s[3])*17)};
        return {double(h(s[1])*16+h(s[2])),double(h(s[3])*16+h(s[4])),double(h(s[5])*16+h(s[6]))};
    }
    if (s.rfind("color(",0)==0) {
        size_t e=s.find(')'); std::string body=s.substr(6,e==std::string::npos?std::string::npos:e-6);
        size_t sp=body.find(' '); std::string space=sp==std::string::npos?body:body.substr(0,sp);
        auto v=nums(sp==std::string::npos?std::string():body.substr(sp+1));
        if(v.size()>=3){ if(space=="display-p3") return p3_color(v[0],v[1],v[2]); if(space=="srgb") return {(double)std::lround(v[0]*255),(double)std::lround(v[1]*255),(double)std::lround(v[2]*255)}; }
        return {217,180,90};
    }
    if (s.rfind("lab(",0)==0 || s.rfind("lch(",0)==0) {
        size_t e=s.find(')'); std::string z=s.substr(4, e==std::string::npos?std::string::npos:e-4);
        for(char& c:z) if(c=='%') c=' ';
        auto v=nums(z); if(v.size()<3) return {217,180,90};
        if(s.rfind("lch(",0)==0){ double h=v[2]*3.14159265358979323846/180; return lab_color(v[0],std::cos(h)*v[1],std::sin(h)*v[1]); }
        return lab_color(v[0],v[1],v[2]);
    }
    if (s.rfind("oklab(",0)==0 || s.rfind("oklch(",0)==0) {
        size_t e=s.find(')'); std::string z=s.substr(6, e==std::string::npos?std::string::npos:e-6);
        for(char& c:z) if(c=='%') c=' ';
        auto v=nums(z); if(v.size()<3) return {217,180,90};
        if(s.rfind("oklch(",0)==0){ double h=v[2]*3.14159265358979323846/180; return oklab_color(v[0],std::cos(h)*v[1],std::sin(h)*v[1]); }
        return oklab_color(v[0],v[1],v[2]);
    }
    auto v=nums(s); if(v.size()<3) return {217,180,90}; return {v[0],v[1],v[2]};
}

static void add_layer(std::vector<std::string>& names, std::vector<std::string>& blends, std::map<std::string,int>& idx, const std::string& n, const std::string& b) {
    if (idx.find(n)!=idx.end()) return;
    idx[n]=(int)names.size(); names.push_back(n); blends.push_back(b.empty()?"normal":b);
}


static std::map<std::string,std::map<std::string,std::string>> load_css(pugi::xml_node root) {
    std::map<std::string,std::map<std::string,std::string>> css;
    for (pugi::xpath_node xn : root.select_nodes("//style")) {
        pugi::xml_node el=xn.node();
        if (std::string(el.name())=="style") {
            std::string t=el.text().as_string(); size_t i=0;
            while ((i=t.find('{',i))!=std::string::npos) {
                size_t e=t.find('}',i); if(e==std::string::npos)break;
                std::string sels=t.substr(0,i), body=t.substr(i+1,e-i-1);
                std::map<std::string,std::string> d;
                size_t q=0;
                while(q<body.size()){ size_t sc=body.find(';',q); if(sc==std::string::npos)sc=body.size(); std::string kv=body.substr(q,sc-q); size_t co=kv.find(':'); if(co!=std::string::npos){ std::string k=kv.substr(0,co),v=kv.substr(co+1); auto trim=[](std::string z){size_t a=z.find_first_not_of(" \t\r\n");size_t b=z.find_last_not_of(" \t\r\n");return a==std::string::npos?std::string():z.substr(a,b-a+1);}; d[trim(k)]=trim(v);} q=sc+1; }
                size_t p=0;
                while(p<sels.size()){ size_t c=sels.find(',',p); if(c==std::string::npos)c=sels.size(); std::string sel=sels.substr(p,c-p); auto trim=[](std::string z){size_t a=z.find_first_not_of(" \t\r\n");size_t b=z.find_last_not_of(" \t\r\n");return a==std::string::npos?std::string():z.substr(a,b-a+1);}; sel=trim(sel); if(!sel.empty()) for(auto&kv:d) css[sel][kv.first]=kv.second; p=c+1; }
                i=e+1;
            }
        }
    }
    return css;
}
static bool selector_match(const std::string& sel, pugi::xml_node ch) {
    if (sel=="*") return true;
    if (sel.size()>1 && sel[0]=='.') { std::string c=ch.attribute("class").as_string(); return (" "+c+" ").find(" "+sel.substr(1)+" ")!=std::string::npos; }
    if (sel.size()>1 && sel[0]=='#') return std::string(ch.attribute("id").as_string())==sel.substr(1);
    return std::string(ch.name())==sel;
}
static std::string css_attr(pugi::xml_node ch, const std::map<std::string,std::map<std::string,std::string>>& css, const std::string& key) {
    std::string v=ch.attribute(key.c_str()).as_string();
    if(!v.empty()) return v;
    std::string st=ch.attribute("style").as_string(); size_t q=0;
    while(q<st.size()){ size_t sc=st.find(';',q); if(sc==std::string::npos)sc=st.size(); std::string kv=st.substr(q,sc-q); size_t co=kv.find(':'); if(co!=std::string::npos){ std::string k=kv.substr(0,co),x=kv.substr(co+1); auto trim=[](std::string z){size_t a=z.find_first_not_of(" \t\r\n");size_t b=z.find_last_not_of(" \t\r\n");return a==std::string::npos?std::string():z.substr(a,b-a+1);}; if(trim(k)==key)return trim(x);} q=sc+1; }
    for(auto& r:css) if(selector_match(r.first,ch)){ auto it=r.second.find(key); if(it!=r.second.end()) return it->second; }
    return "";
}
static std::map<std::string,std::string> g_grad_paint;
static std::map<std::string,std::string> load_grads(pugi::xml_node root) {
    std::map<std::string,std::string> out;
    g_grad_paint.clear();
    std::function<void(pugi::xml_node)> scan=[&](pugi::xml_node n){ for(pugi::xml_node ch=n.first_child();ch;ch=ch.next_sibling()){ std::string tag=ch.name(); if(tag=="linearGradient"||tag=="radialGradient"){ std::string id=ch.attribute("id").as_string(); if(!id.empty()){ double r=0,g=0,b=0; int c=0; std::string pj="{\"type\":\""+std::string(tag=="radialGradient"?"radial":"linear")+"\",\"units\":\""+std::string(ch.attribute("gradientUnits").as_string("objectBoundingBox"))+"\",\"stops\":[";
        bool first=true;
        for(pugi::xml_node st=ch.first_child();st;st=st.next_sibling()){ if(std::string(st.name())=="stop"){ auto v=color_from(st.attribute("stop-color").as_string()); if(v.size()>=3){ r+=v[0];g+=v[1];b+=v[2];c++;
            if(!first)pj+=","; first=false;
            pj+="["+fmt(std::atof(st.attribute("offset").as_string("0")))+",["+fmt(v[0])+","+fmt(v[1])+","+fmt(v[2])+"],"+fmt(std::atof(st.attribute("stop-opacity").as_string("1")))+"]";
        } } }
        pj+="],\"source\":{\"gradient\":{\"attrs\":{";
        bool fa=true;
        for(const char* k: {"gradientTransform","spreadMethod","fx","fy"}){ std::string v=ch.attribute(k).as_string(); if(!v.empty()){ if(!fa)pj+=","; fa=false; pj+="\""+std::string(k)+"\":\""+v+"\""; } }
        pj+="}}}}";
        if(tag=="radialGradient"){ pj+=",\"cx\":\""+std::string(ch.attribute("cx").as_string("50%"))+"\",\"cy\":\""+std::string(ch.attribute("cy").as_string("50%"))+"\",\"r\":\""+std::string(ch.attribute("r").as_string("50%"))+"\""; }
        else { pj+=",\"x1\":\""+std::string(ch.attribute("x1").as_string("0%"))+"\",\"y1\":\""+std::string(ch.attribute("y1").as_string("0%"))+"\",\"x2\":\""+std::string(ch.attribute("x2").as_string("100%"))+"\",\"y2\":\""+std::string(ch.attribute("y2").as_string("0%"))+"\""; }
        g_grad_paint[id]=pj;
        if(c){ char hex[8]; std::snprintf(hex,sizeof(hex),"#%02x%02x%02x",(int)std::lround(r/c),(int)std::lround(g/c),(int)std::lround(b/c)); out[id]=hex; } } } scan(ch);} };
    scan(root); return out;
}
static std::string resolve_paint(const std::string& v, const std::map<std::string,std::string>& grads) {
    if(v.rfind("url(#",0)==0){ size_t e=v.find(')',5); std::string id=v.substr(5,e-5); auto it=grads.find(id); if(it!=grads.end())return it->second; }
    return v;
}
static void shape_points(const std::string& tag, pugi::xml_node ch, const std::map<std::string,std::map<std::string,std::string>>& css, std::vector<double>& xs, std::vector<double>& ys) {
    auto f=[&](const char*k,double d=0){ std::string v=css_attr(ch,css,k); return v.empty()?d:std::atof(v.c_str()); };
    if(tag=="circle"){ double cx=f("cx"),cy=f("cy"),r=f("r"); for(int i=0;i<32;i++){xs.push_back(cx+r*std::cos(2*3.14159265358979323846*i/32));ys.push_back(cy+r*std::sin(2*3.14159265358979323846*i/32));} xs.push_back(cx+r);ys.push_back(cy); }
    else if(tag=="ellipse"){ double cx=f("cx"),cy=f("cy"),rx=f("rx"),ry=f("ry"); for(int i=0;i<32;i++){xs.push_back(cx+rx*std::cos(2*3.14159265358979323846*i/32));ys.push_back(cy+ry*std::sin(2*3.14159265358979323846*i/32));} xs.push_back(cx+rx);ys.push_back(cy); }
    else if(tag=="rect"){ double x=f("x"),y=f("y"),w=f("width"),h=f("height"); xs={x,x+w,x+w,x,x}; ys={y,y,y+h,y+h,y}; }
    else if(tag=="polygon"||tag=="polyline"){ auto v=nums(css_attr(ch,css,"points")); for(size_t i=0;i+1<v.size();i+=2){xs.push_back(v[i]);ys.push_back(v[i+1]);} if(tag=="polygon"&&!xs.empty()){xs.push_back(xs[0]);ys.push_back(ys[0]);} }
}


typedef std::vector<std::pair<double,double>> Poly;


static bool is_convex(const Poly& poly){
    Poly p=poly; if(p.size()>3&&p[0]==p.back())p.pop_back(); int n=(int)p.size(); int sign=0;
    for(int i=0;i<n;++i){auto a=p[i],b=p[(i+1)%n],c=p[(i+2)%n];double cr=(b.first-a.first)*(c.second-a.second)-(b.second-a.second)*(c.first-a.first);if(std::fabs(cr)<1e-12)continue;int sg=cr>0?1:-1;if(sign&&sg!=sign)return false;sign=sg;}
    return true;
}

static std::vector<Poly> triangulate(const Poly& poly) {
    Poly pts=poly; if(pts.size()>3 && pts[0]==pts.back())pts.pop_back();
    int n=(int)pts.size(); if(n<3)return {};
    double area=0; for(int i=0;i<n;++i){auto a=pts[i],b=pts[(i+1)%n];area+=a.first*b.second-b.first*a.second;}
    if(area<0)std::reverse(pts.begin(),pts.end());
    std::vector<int> idx(n); for(int i=0;i<n;++i)idx[i]=i;
    std::vector<Poly> out; int guard=0;
    while((int)idx.size()>3 && guard<n*n){
        guard++; bool ear=false;
        for(size_t k=0;k<idx.size();++k){
            int i0=idx[(k+idx.size()-1)%idx.size()], i1=idx[k], i2=idx[(k+1)%idx.size()];
            auto a=pts[i0],b=pts[i1],c=pts[i2];
            double cross=(b.first-a.first)*(c.second-a.second)-(b.second-a.second)*(c.first-a.first);
            if(cross<=1e-12)continue;
            bool inside=false;
            for(int j:idx){ if(j==i0||j==i1||j==i2)continue; auto q=pts[j];
                double d1=(b.first-a.first)*(q.second-a.second)-(b.second-a.second)*(q.first-a.first);
                double d2=(c.first-b.first)*(q.second-b.second)-(c.second-b.second)*(q.first-b.first);
                double d3=(a.first-c.first)*(q.second-c.second)-(a.second-c.second)*(q.first-c.first);
                if(d1>=0&&d2>=0&&d3>=0){inside=true;break;}
            }
            if(!inside){out.push_back({a,b,c}); idx.erase(idx.begin()+k); ear=true; break;}
        }
        if(!ear)break;
    }
    if(idx.size()==3)out.push_back({pts[idx[0]],pts[idx[1]],pts[idx[2]]});
    return out;
}

static Poly clip_poly(const Poly& subj, const Poly& clip) {
    if(subj.size()<3||clip.size()<3)return {};
    double area=0; for(size_t i=0;i<clip.size();++i){auto a=clip[i],b=clip[(i+1)%clip.size()];area+=a.first*b.second-b.first*a.second;}
    Poly poly=clip; if(area<0)std::reverse(poly.begin(),poly.end());
    Poly out=subj;
    for(size_t i=0;i<poly.size();++i){
        auto a=poly[i], b=poly[(i+1)%poly.size()]; double ex=b.first-a.first, ey=b.second-a.second;
        auto inside=[&](const std::pair<double,double>& p){return ex*(p.second-a.second)-ey*(p.first-a.first)>=-1e-9;};
        auto crossp=[&](const std::pair<double,double>& p,const std::pair<double,double>& q){
            double dx=q.first-p.first, dy=q.second-p.second, den=ex*dy-ey*dx;
            if(std::fabs(den)<1e-12)return p;
            double t=(ex*(a.second-p.second)-ey*(a.first-p.first))/den;
            return std::make_pair(p.first+dx*t,p.second+dy*t);
        };
        Poly inp=out; out.clear(); if(inp.empty())break;
        for(size_t j=0;j<inp.size();++j){auto cur=inp[j],prev=inp[(j+inp.size()-1)%inp.size()]; if(inside(cur)){if(!inside(prev))out.push_back(crossp(prev,cur));out.push_back(cur);} else if(inside(prev))out.push_back(crossp(prev,cur));}
    }
    return out;
}
static bool clip_seg(std::pair<double,double> p0,std::pair<double,double> p1,const Poly& clip,std::pair<double,double>& a,std::pair<double,double>& b){
    double area=0; for(size_t i=0;i<clip.size();++i){auto u=clip[i],v=clip[(i+1)%clip.size()];area+=u.first*v.second-v.first*u.second;}
    Poly poly=clip; if(area<0)std::reverse(poly.begin(),poly.end());
    double t0=0,t1=1,dx=p1.first-p0.first,dy=p1.second-p0.second;
    for(size_t i=0;i<poly.size();++i){auto u=poly[i],v=poly[(i+1)%poly.size()];double ex=v.first-u.first,ey=v.second-u.second;
        double num=ex*(p0.second-u.second)-ey*(p0.first-u.first), den=ex*dy-ey*dx;
        if(std::fabs(den)<1e-12){if(num<0)return false;continue;}
        double t=-num/den; if(den>0){if(t>t1)return false;if(t>t0)t0=t;} else {if(t<t0)return false;if(t<t1)t1=t;}
    }
    if(t0>t1)return false; a={p0.first+dx*t0,p0.second+dy*t0}; b={p0.first+dx*t1,p0.second+dy*t1}; return true;
}
struct ClipSet { std::vector<Poly> polys; bool evenodd=false; };
static std::map<std::string,ClipSet> load_clips(pugi::xml_node root){
    std::map<std::string,ClipSet> out;
    for(pugi::xpath_node xn:root.select_nodes("//*")){pugi::xml_node el=xn.node(); std::string tag=el.name(); if((tag!="clipPath"&&tag!="mask")||!el.attribute("id"))continue; std::vector<Poly> ps; for(pugi::xml_node ch=el.first_child();ch;ch=ch.next_sibling()){std::string t=ch.name(); std::vector<double> xs,ys; if(t=="path")parse_d(ch.attribute("d").as_string(),xs,ys); else if(t=="rect"||t=="circle"||t=="ellipse"||t=="polygon")shape_points(t,ch,{},xs,ys); if(xs.size()>=3){Poly q; for(size_t i=0;i<xs.size();++i)q.push_back({xs[i],ys[i]});ps.push_back(q);} } if(!ps.empty()){ClipSet cs; cs.polys=ps; cs.evenodd=std::string(el.attribute("clip-rule").as_string())=="evenodd"; if(!cs.evenodd){for(pugi::xml_node q=el.first_child();q;q=q.next_sibling()){if(std::string(q.attribute("clip-rule").as_string())=="evenodd"){cs.evenodd=true;break;}}} out[el.attribute("id").as_string()]=cs;}}
    return out;
}
static std::string clip_url(const std::string& v){ if(v.rfind("url(#",0)==0){size_t e=v.find(')',5);return v.substr(5,e-5);} return ""; }

static std::string b64enc(const unsigned char* d, size_t n) {
    static const char* T = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    std::string o; o.reserve((n + 2) / 3 * 4);
    for (size_t i = 0; i < n; i += 3) {
        unsigned v = (unsigned)d[i] << 16;
        if (i + 1 < n) v |= (unsigned)d[i+1] << 8;
        if (i + 2 < n) v |= (unsigned)d[i+2];
        o += T[(v >> 18) & 63]; o += T[(v >> 12) & 63];
        o += i + 1 < n ? T[(v >> 6) & 63] : '=';
        o += i + 2 < n ? T[v & 63] : '=';
    }
    return o;
}


static int gdi_encoder_clsid(const wchar_t* mime, CLSID* out) {
    UINT n = 0, sz = 0;
    Gdiplus::GetImageEncodersSize(&n, &sz);
    if (!sz) return 0;
    std::vector<BYTE> buf(sz);
    auto* codecs = (Gdiplus::ImageCodecInfo*)buf.data();
    Gdiplus::GetImageEncoders(n, sz, codecs);
    for (UINT i = 0; i < n; ++i) if (wcscmp(codecs[i].MimeType, mime) == 0) { *out = codecs[i].Clsid; return 1; }
    return 0;
}

struct GdiSave {
    Gdiplus::Graphics& g; Gdiplus::GraphicsState st;
    explicit GdiSave(Gdiplus::Graphics& gg) : g(gg), st(gg.Save()) {}
    ~GdiSave() { g.Restore(st); }
};

static std::wstring u2w(const std::string& s) {
    if (s.empty()) return L"";
    int n = MultiByteToWideChar(CP_UTF8, 0, s.c_str(), (int)s.size(), nullptr, 0);
    std::wstring w(n, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s.c_str(), (int)s.size(), &w[0], n);
    return w;
}

static pugi::xml_node g_gdi_root;

static std::unique_ptr<Gdiplus::Brush> gdi_brush_for(pugi::xml_node n, const std::string& fill, double sx, double sy) {
    if (fill.rfind("url(#",0)==0) {
        size_t e=fill.find(')',5); std::string id=fill.substr(5,e-5);
        pugi::xml_node grad;
        for (pugi::xpath_node xn : g_gdi_root.select_nodes("//linearGradient | //radialGradient")) {
            if (std::string(xn.node().attribute("id").as_string())==id) { grad=xn.node(); break; }
        }
        if (grad) {
            std::vector<Gdiplus::Color> cols; std::vector<Gdiplus::REAL> pos;
            for (pugi::xml_node st : grad.children()) {
                if (std::string(st.name())!="stop") continue;
                std::string off=st.attribute("offset").as_string("0");
                double o=0; try { o = off.back()=='%' ? std::atof(off.substr(0,off.size()-1).c_str())/100.0 : std::atof(off.c_str()); } catch(...) {}
                auto c=color_from(st.attribute("stop-color").as_string("#000"));
                double aa=st.attribute("stop-opacity")?std::atof(st.attribute("stop-opacity").as_string()):1.0;
                cols.push_back(Gdiplus::Color((BYTE)(aa*255),(BYTE)c[0],(BYTE)c[1],(BYTE)c[2]));
                pos.push_back((Gdiplus::REAL)o);
            }
            if (cols.size()>=2) {
                double x=0,y=0,w=100,h=100;
                if (std::string(n.name())=="rect") {
                    x=std::atof(n.attribute("x").as_string("0")); y=std::atof(n.attribute("y").as_string("0"));
                    w=std::atof(n.attribute("width").as_string("100")); h=std::atof(n.attribute("height").as_string("100"));
                }
                Gdiplus::RectF bb((Gdiplus::REAL)(x*sx),(Gdiplus::REAL)(y*sy),(Gdiplus::REAL)std::max(1.0,w*sx),(Gdiplus::REAL)std::max(1.0,h*sy));
                if (std::string(grad.name()) == "radialGradient") {
                    double cx = grad.attribute("cx") ? std::atof(grad.attribute("cx").as_string()) : 0.5;
                    double cy = grad.attribute("cy") ? std::atof(grad.attribute("cy").as_string()) : 0.5;
                    double rr = grad.attribute("r") ? std::atof(grad.attribute("r").as_string()) : 0.5;
                    Gdiplus::GraphicsPath path;
                    Gdiplus::REAL cxp = bb.X + (Gdiplus::REAL)(cx * bb.Width);
                    Gdiplus::REAL cyp = bb.Y + (Gdiplus::REAL)(cy * bb.Height);
                    Gdiplus::REAL rp = (Gdiplus::REAL)std::max(1.0, rr * std::min(bb.Width, bb.Height));
                    if (std::string(n.name()) == "rect") path.AddRectangle(bb);
                    else path.AddEllipse(cxp - rp, cyp - rp, rp*2, rp*2);
                    auto* brush = new Gdiplus::PathGradientBrush(&path);
                    brush->SetCenterPoint(Gdiplus::PointF(cxp, cyp));
                    std::vector<Gdiplus::Color> rcols(cols.rbegin(), cols.rend());
                    std::vector<Gdiplus::REAL> rpos;
                    for (auto it = pos.rbegin(); it != pos.rend(); ++it) rpos.push_back(1.0f - *it);
                    brush->SetCenterColor(rcols.front());
                    std::vector<Gdiplus::Color> surround(rcols.size(), rcols.back());
                    INT scount = (INT)surround.size();
                    brush->SetSurroundColors(surround.data(), &scount);
                    brush->SetInterpolationColors(rcols.data(), rpos.data(), (INT)rcols.size());
                    return std::unique_ptr<Gdiplus::Brush>(brush);
                }
                double gx1=std::atof(grad.attribute("x1").as_string("0")), gy1=std::atof(grad.attribute("y1").as_string("0"));
                double gx2=std::atof(grad.attribute("x2").as_string("1")), gy2=std::atof(grad.attribute("y2").as_string("0"));
                double ang=std::atan2((gy2-gy1)*bb.Height, (gx2-gx1)*bb.Width) * 180.0 / 3.14159265358979323846;
                auto* brush = new Gdiplus::LinearGradientBrush(bb, cols[0], cols.back(), (Gdiplus::REAL)ang);
                brush->SetInterpolationColors(cols.data(), pos.data(), (INT)cols.size());
                return std::unique_ptr<Gdiplus::Brush>(brush);
            }
        }
    }
    auto c=color_from(fill.empty()||fill=="none"?"#000":fill);
    return std::unique_ptr<Gdiplus::Brush>(new Gdiplus::SolidBrush(Gdiplus::Color(255,(BYTE)c[0],(BYTE)c[1],(BYTE)c[2])));
}

static Mat mat_inv(const Mat& m) {
    double det = m.a*m.d - m.b*m.c;
    if (std::fabs(det) < 1e-12) return Mat();
    return Mat{m.d/det, -m.b/det, -m.c/det, m.a/det,
               (m.c*m.f - m.d*m.e)/det, (m.b*m.e - m.a*m.f)/det};
}
static double grad_len(const std::string& v, double d) {
    std::string t = v;
    if (t.empty()) return d;
    if (!t.empty() && t.back() == '%') return std::atof(t.substr(0, t.size()-1).c_str()) / 100.0;
    return std::atof(t.c_str());
}
static Gdiplus::Color grad_stop_color(pugi::xml_node st) {
    auto c = color_from(st.attribute("stop-color").as_string("#000"));
    double a = st.attribute("stop-opacity") ? std::atof(st.attribute("stop-opacity").as_string()) : 1.0;
    return Gdiplus::Color((BYTE)(a*255), (BYTE)c[0], (BYTE)c[1], (BYTE)c[2]);
}
static Gdiplus::Color grad_eval(pugi::xml_node grad, double t) {
    t = std::max(0.0, std::min(1.0, t));
    Gdiplus::Color prev(0,0,0,0); double po=0; bool have=false;
    for (pugi::xml_node st : grad.children()) {
        if (std::string(st.name())!="stop") continue;
        std::string off=st.attribute("offset").as_string("0");
        double o = (!off.empty() && off.back()=='%') ? std::atof(off.substr(0,off.size()-1).c_str())/100.0 : std::atof(off.c_str());
        Gdiplus::Color c=grad_stop_color(st);
        if (!have) { if (t<=o) return c; prev=c; po=o; have=true; continue; }
        if (t<=o) {
            double u=(t-po)/std::max(1e-9,o-po);
            return Gdiplus::Color((BYTE)(prev.GetA()+(c.GetA()-prev.GetA())*u),
                                  (BYTE)(prev.GetR()+(c.GetR()-prev.GetR())*u),
                                  (BYTE)(prev.GetG()+(c.GetG()-prev.GetG())*u),
                                  (BYTE)(prev.GetB()+(c.GetB()-prev.GetB())*u));
        }
        prev=c; po=o;
    }
    return prev;
}
static double grad_t_at(pugi::xml_node grad, const Mat& inv, double px, double py, double bx, double by, double bw, double bh) {
    bool bbox = std::string(grad.attribute("gradientUnits").as_string("objectBoundingBox"))=="objectBoundingBox";
    double qx=px, qy=py;
    if (bbox) { qx=(px-bx)/std::max(1e-9,bw); qy=(py-by)/std::max(1e-9,bh); }
    double tx=inv.a*qx+inv.c*qy+inv.e, ty=inv.b*qx+inv.d*qy+inv.f;
    if (std::string(grad.name())=="linearGradient") {
        double x1=grad_len(grad.attribute("x1").as_string(),0), y1=grad_len(grad.attribute("y1").as_string(),0);
        double x2=grad_len(grad.attribute("x2").as_string(),1), y2=grad_len(grad.attribute("y2").as_string(),0);
        double gx=x2-x1, gy=y2-y1, gl=gx*gx+gy*gy;
        return gl>1e-12 ? ((tx-x1)*gx+(ty-y1)*gy)/gl : 0.0;
    }
    double cx=grad_len(grad.attribute("cx").as_string(),0.5), cy=grad_len(grad.attribute("cy").as_string(),0.5);
    double rr=grad_len(grad.attribute("r").as_string(),0.5);
    double fx=grad.attribute("fx")?grad_len(grad.attribute("fx").as_string(),cx):cx;
    double fy=grad.attribute("fy")?grad_len(grad.attribute("fy").as_string(),cy):cy;
    double dx=tx-fx, dy=ty-fy, Dx=cx-fx, Dy=cy-fy;
    double A=Dx*Dx+Dy*Dy-rr*rr, B=-2*(dx*Dx+dy*Dy), C=dx*dx+dy*dy;
    if (std::fabs(A)<1e-9) return std::fabs(B)<1e-12?0:-C/B;
    double disc=std::max(0.0,B*B-4*A*C);
    return (-B-std::sqrt(disc))/(2*A);
}
static bool gdi_shape_path(pugi::xml_node n, Gdiplus::GraphicsPath& path, Gdiplus::RectF& bb, double sx, double sy) {
    std::string tag = n.name();
    double x=std::atof(n.attribute("x").as_string("0")), y=std::atof(n.attribute("y").as_string("0"));
    double w=std::atof(n.attribute("width").as_string("0")), h=std::atof(n.attribute("height").as_string("0"));
    if (tag=="rect") {
        if (w<=0||h<=0) return false;
        path.AddRectangle(Gdiplus::RectF((Gdiplus::REAL)(x*sx),(Gdiplus::REAL)(y*sy),(Gdiplus::REAL)(w*sx),(Gdiplus::REAL)(h*sy)));
    } else if (tag=="circle" || tag=="ellipse") {
        double cx=std::atof(n.attribute("cx").as_string("0")), cy=std::atof(n.attribute("cy").as_string("0"));
        double rx=tag=="circle"?std::atof(n.attribute("r").as_string("0")):std::atof(n.attribute("rx").as_string("0"));
        double ry=tag=="circle"?rx:std::atof(n.attribute("ry").as_string("0"));
        if (rx<=0||ry<=0) return false;
        path.AddEllipse(Gdiplus::RectF((Gdiplus::REAL)((cx-rx)*sx),(Gdiplus::REAL)((cy-ry)*sy),(Gdiplus::REAL)(2*rx*sx),(Gdiplus::REAL)(2*ry*sy)));
    } else if (tag=="polygon" || tag=="path") {
        std::vector<double> xs, ys;
        if (tag=="polygon") {
            auto v=nums(n.attribute("points").as_string());
            for (size_t i=0;i+1<v.size();i+=2){xs.push_back(v[i]);ys.push_back(v[i+1]);}
        } else parse_d(n.attribute("d").as_string(), xs, ys);
        if (xs.size()<3) return false;
        std::vector<Gdiplus::PointF> pts;
        for (size_t i=0;i<xs.size();++i) pts.push_back(Gdiplus::PointF((Gdiplus::REAL)(xs[i]*sx),(Gdiplus::REAL)(ys[i]*sy)));
        path.AddPolygon(pts.data(), (INT)pts.size());
    } else return false;
    path.GetBounds(&bb);
    return bb.Width>0 && bb.Height>0;
}

static bool gdi_gradient_shape(Gdiplus::Graphics& g, pugi::xml_node n, pugi::xml_node grad, double sx, double sy) {
    Gdiplus::GraphicsPath path; Gdiplus::RectF bb;
    if (!gdi_shape_path(n, path, bb, sx, sy)) return false;
    double x=bb.X/sx, y=bb.Y/sy, w=bb.Width/sx, h=bb.Height/sy;
    Mat M=parse_transform(grad.attribute("gradientTransform").as_string());
    Mat inv=mat_inv(M);
    int W=std::max(1,(int)std::lround(bb.Width)), H=std::max(1,(int)std::lround(bb.Height));
    Gdiplus::Bitmap bmp(W,H,PixelFormat32bppARGB);
    Gdiplus::Rect rc(0,0,W,H); Gdiplus::BitmapData bd{};
    if (bmp.LockBits(&rc,Gdiplus::ImageLockModeWrite,PixelFormat32bppARGB,&bd)!=Gdiplus::Ok) return false;
    for (int j=0;j<H;++j) {
        BYTE* row=(BYTE*)bd.Scan0 + (size_t)j*bd.Stride;
        for (int i=0;i<W;++i) {
            double px=x+(i+0.5)/sx, py=y+(j+0.5)/sy;
            double t=grad_t_at(grad,inv,px,py,x,y,w,h);
            Gdiplus::Color c=grad_eval(grad,t);
            row[i*4+0]=c.GetB(); row[i*4+1]=c.GetG(); row[i*4+2]=c.GetR(); row[i*4+3]=c.GetA();
        }
    }
    bmp.UnlockBits(&bd);
    Gdiplus::TextureBrush brush(&bmp);
    brush.TranslateTransform(bb.X, bb.Y);
    g.FillPath(&brush, &path);
    return true;
}

static void gdi_draw_node(Gdiplus::Graphics& g, pugi::xml_node n, double sx, double sy) {
    GdiSave save(g);
    std::string tr = n.attribute("transform").as_string();
    if (!tr.empty()) {
        Mat m = parse_transform(tr);
        Gdiplus::Matrix gm((Gdiplus::REAL)m.a, (Gdiplus::REAL)m.b, (Gdiplus::REAL)m.c,
                           (Gdiplus::REAL)m.d, (Gdiplus::REAL)m.e, (Gdiplus::REAL)m.f);
        g.MultiplyTransform(&gm, Gdiplus::MatrixOrderAppend);
    }
    std::string tag = n.name();
    auto num = [&](const char* k, double d){ auto a=n.attribute(k); return a ? std::atof(a.as_string()) : d; };
    std::string fill = n.attribute("fill").as_string();
    if (fill.empty() || fill == "none") fill = "#000";
    if ((tag == "rect" || tag == "circle" || tag == "ellipse" || tag == "polygon" || tag == "path") && fill.rfind("url(#",0)==0) {
        size_t e=fill.find(')',5); std::string gid=fill.substr(5,e-5);
        for (pugi::xpath_node xn : g_gdi_root.select_nodes("//linearGradient | //radialGradient")) {
            pugi::xml_node gg=xn.node();
            if (std::string(gg.attribute("id").as_string())==gid && gg.attribute("gradientTransform")) {
                if (gdi_gradient_shape(g,n,gg,sx,sy)) return;
            }
        }
    }
    auto br = gdi_brush_for(n, fill, sx, sy);
    if (tag == "g") { for (auto ch : n.children()) gdi_draw_node(g, ch, sx, sy); return; }
    if (tag == "text") {
        std::string txt = n.text().get();
        if (txt.empty()) return;
        double fs = n.attribute("font-size") ? std::atof(n.attribute("font-size").as_string()) : 16.0;
        std::string fam = n.attribute("font-family") ? n.attribute("font-family").as_string() : "Arial";
        std::wstring wfam = u2w(fam);
        Gdiplus::Font font(wfam.c_str(), (Gdiplus::REAL)(fs * sy), Gdiplus::FontStyleRegular, Gdiplus::UnitPixel);
        g.DrawString(u2w(txt).c_str(), -1, &font, Gdiplus::PointF((Gdiplus::REAL)(num("x",0)*sx), (Gdiplus::REAL)(num("y",0)*sy)), br.get());
        return;
    }
    if (tag == "use") {
        std::string href = n.attribute("href").as_string();
        if (href.empty()) href = std::string(n.attribute("{http://www.w3.org/1999/xlink}href").as_string());
        if (href.size() > 1 && href[0] == '#') {
            pugi::xml_node target;
            for (pugi::xml_node q : g_gdi_root.children()) {
                if (std::string(q.attribute("id").as_string()) == href.substr(1)) { target = q; break; }
            }
            if (!target) for (pugi::xpath_node xn : g_gdi_root.select_nodes("//*")) {
                if (std::string(xn.node().attribute("id").as_string()) == href.substr(1)) { target = xn.node(); break; }
            }
            if (target) {
                GdiSave u(g);
                double ux = num("x",0)*sx, uy = num("y",0)*sy;
                Gdiplus::Matrix tm(1,0,0,1,(Gdiplus::REAL)ux,(Gdiplus::REAL)uy);
                g.MultiplyTransform(&tm, Gdiplus::MatrixOrderAppend);
                if (std::string(target.name()) == "symbol" || std::string(target.name()) == "g") {
                    for (auto ch : target.children()) gdi_draw_node(g, ch, sx, sy);
                } else {
                    gdi_draw_node(g, target, sx, sy);
                }
            }
        }
        return;
    }
    if (tag == "image") {
        std::string href = n.attribute("href").as_string();
        if (href.empty()) href = std::string(n.attribute("{http://www.w3.org/1999/xlink}href").as_string());
        if (href.empty()) return;
        Gdiplus::Bitmap* bmp = nullptr;
        if (href.rfind("data:", 0) == 0) {
            size_t comma = href.find(',');
            if (comma == std::string::npos) return;
            std::string b64 = href.substr(comma + 1);
            DWORD n = 0;
            CryptStringToBinaryA(b64.c_str(), 0, CRYPT_STRING_BASE64, nullptr, &n, nullptr, nullptr);
            std::vector<BYTE> buf(n);
            if (n && CryptStringToBinaryA(b64.c_str(), 0, CRYPT_STRING_BASE64, buf.data(), &n, nullptr, nullptr)) {
                IStream* st = nullptr;
                if (SUCCEEDED(CreateStreamOnHGlobal(nullptr, TRUE, &st))) {
                    st->Write(buf.data(), (ULONG)n, nullptr);
                    LARGE_INTEGER z{}; st->Seek(z, STREAM_SEEK_SET, nullptr);
                    bmp = new Gdiplus::Bitmap(st);
                    st->Release();
                }
            }
        } else {
            std::wstring wp = u2w((g_base_dir.empty() ? "" : g_base_dir + "/") + href);
            bmp = new Gdiplus::Bitmap(wp.c_str());
        }
        if (bmp && bmp->GetLastStatus() == Gdiplus::Ok) {
            double x = num("x",0)*sx, y = num("y",0)*sy;
            double w = num("width",0)*sx, h = num("height",0)*sy;
            if (w <= 0) w = bmp->GetWidth() * sx;
            if (h <= 0) h = bmp->GetHeight() * sy;
            g.DrawImage(bmp, Gdiplus::RectF((Gdiplus::REAL)x,(Gdiplus::REAL)y,(Gdiplus::REAL)w,(Gdiplus::REAL)h));
        }
        delete bmp;
        return;
    }
    if (tag == "rect") {
        g.FillRectangle(br.get(), (Gdiplus::REAL)(num("x",0)*sx), (Gdiplus::REAL)(num("y",0)*sy),
                        (Gdiplus::REAL)(num("width",0)*sx), (Gdiplus::REAL)(num("height",0)*sy));
    } else if (tag == "circle") {
        double cx=num("cx",0), cy=num("cy",0), r=num("r",0);
        g.FillEllipse(br.get(), (Gdiplus::REAL)((cx-r)*sx), (Gdiplus::REAL)((cy-r)*sy),
                      (Gdiplus::REAL)(2*r*sx), (Gdiplus::REAL)(2*r*sy));
    } else if (tag == "ellipse") {
        double cx=num("cx",0), cy=num("cy",0), rx=num("rx",0), ry=num("ry",0);
        g.FillEllipse(br.get(), (Gdiplus::REAL)((cx-rx)*sx), (Gdiplus::REAL)((cy-ry)*sy),
                      (Gdiplus::REAL)(2*rx*sx), (Gdiplus::REAL)(2*ry*sy));
    } else if (tag == "polygon" || tag == "path") {
        std::vector<double> xs, ys;
        if (tag == "polygon") {
            std::string pts=n.attribute("points").as_string();
            auto v=nums(pts);
            for (size_t i=0;i+1<v.size();i+=2){xs.push_back(v[i]);ys.push_back(v[i+1]);}
        } else parse_d(n.attribute("d").as_string(), xs, ys);
        if (xs.size() >= 3) {
            std::vector<Gdiplus::PointF> p;
            for (size_t i=0;i<xs.size();++i) p.push_back(Gdiplus::PointF((Gdiplus::REAL)(xs[i]*sx),(Gdiplus::REAL)(ys[i]*sy)));
            g.FillPolygon(br.get(), p.data(), (INT)p.size());
        }
    }
}

static std::string gdi_pattern_png(pugi::xml_node pat, int& out_w, int& out_h, std::string& units) {
    units = pat.attribute("patternUnits").as_string();
    if (units.empty()) units = "objectBoundingBox";
    double w = 32, h = 32;
    auto wa = pat.attribute("width"), ha = pat.attribute("height");
    if (wa) w = std::atof(wa.as_string());
    if (ha) h = std::atof(ha.as_string());
    double sx=1, sy=1;
    if (units == "objectBoundingBox") { sx = 256; sy = 256; }
    int pw = std::max(1, (int)std::lround(w * sx)), ph = std::max(1, (int)std::lround(h * sy));
    if (pw > 2048) pw = 2048; if (ph > 2048) ph = 2048;
    out_w = pw; out_h = ph;
    Gdiplus::Bitmap bmp(pw, ph, PixelFormat32bppARGB);
    Gdiplus::Graphics g(&bmp);
    g.SetSmoothingMode(Gdiplus::SmoothingModeAntiAlias);
    g.Clear(Gdiplus::Color(0,0,0,0));
    for (auto ch : pat.children()) gdi_draw_node(g, ch, sx, sy);
    CLSID png; if (!gdi_encoder_clsid(L"image/png", &png)) return "";
    IStream* stream=nullptr;
    if (FAILED(CreateStreamOnHGlobal(nullptr, TRUE, &stream))) return "";
    if (bmp.Save(stream, &png, nullptr) != Gdiplus::Ok) { stream->Release(); return ""; }
    STATSTG st{}; stream->Stat(&st, STATFLAG_NONAME);
    HGLOBAL hmem=nullptr; GetHGlobalFromStream(stream, &hmem);
    void* data=GlobalLock(hmem);
    std::string out = b64enc((const unsigned char*)data, (size_t)st.cbSize.QuadPart);
    GlobalUnlock(hmem); stream->Release();
    return out;
}

static void gdi_blur_argb(std::vector<unsigned char>& px, int w, int h, int rad) {
    if (rad <= 0) return;
    std::vector<unsigned char> tmp(px.size());
    for (int pass = 0; pass < 3; ++pass) {
        for (int y = 0; y < h; ++y) for (int x = 0; x < w; ++x) {
            int sr=0,sg=0,sb=0,sa=0,n=0;
            for (int k=-rad;k<=rad;++k) { int xx=x+k; if(xx<0||xx>=w)continue; size_t i=((size_t)y*w+xx)*4; sr+=px[i];sg+=px[i+1];sb+=px[i+2];sa+=px[i+3];n++; }
            size_t o=((size_t)y*w+x)*4; tmp[o]=(unsigned char)(sr/n);tmp[o+1]=(unsigned char)(sg/n);tmp[o+2]=(unsigned char)(sb/n);tmp[o+3]=(unsigned char)(sa/n);
        }
        px.swap(tmp);
        for (int y = 0; y < h; ++y) for (int x = 0; x < w; ++x) {
            int sr=0,sg=0,sb=0,sa=0,n=0;
            for (int k=-rad;k<=rad;++k) { int yy=y+k; if(yy<0||yy>=h)continue; size_t i=((size_t)yy*w+x)*4; sr+=px[i];sg+=px[i+1];sb+=px[i+2];sa+=px[i+3];n++; }
            size_t o=((size_t)y*w+x)*4; tmp[o]=(unsigned char)(sr/n);tmp[o+1]=(unsigned char)(sg/n);tmp[o+2]=(unsigned char)(sb/n);tmp[o+3]=(unsigned char)(sa/n);
        }
        px.swap(tmp);
    }
}

static std::string gdi_filter_png(pugi::xml_node el, pugi::xml_node root, int W, int H, int& ow, int& oh) {
    std::string fref = el.attribute("filter").as_string();
    if (fref.empty()) return "";
    size_t hp = fref.find('#');
    std::string fid = hp == std::string::npos ? fref : fref.substr(hp + 1);
    if (!fid.empty() && fid.back() == ')') fid.pop_back();
    g_gdi_root = root;
    pugi::xml_node fel;
    for (pugi::xpath_node xn : root.select_nodes("//filter")) if (std::string(xn.node().attribute("id").as_string()) == fid) fel = xn.node();
    if (!fel) return "";
    ow = W; oh = H;
    Gdiplus::Bitmap bmp(W, H, PixelFormat32bppARGB);
    Gdiplus::Graphics g(&bmp);
    g.SetSmoothingMode(Gdiplus::SmoothingModeAntiAlias);
    g.Clear(Gdiplus::Color(0,0,0,0));
    gdi_draw_node(g, el, 1.0, 1.0);
    Gdiplus::Rect rc(0,0,W,H);
    Gdiplus::BitmapData bd{};
    if (bmp.LockBits(&rc, (Gdiplus::ImageLockModeRead | Gdiplus::ImageLockModeWrite), PixelFormat32bppARGB, &bd) != Gdiplus::Ok) return "";
    std::vector<unsigned char> px((size_t)W*H*4);
    for (int y=0;y<H;++y) std::memcpy(px.data()+(size_t)y*W*4, (unsigned char*)bd.Scan0+(size_t)y*bd.Stride, (size_t)W*4);
    bmp.UnlockBits(&bd);
    int ox=0, oy=0;
    for (auto prim : fel.children()) {
        std::string t = prim.name();
        if (t == "feGaussianBlur") {
            int rad = std::max(1, (int)std::lround(std::atof(prim.attribute("stdDeviation").as_string())));
            gdi_blur_argb(px, W, H, rad);
        } else if (t == "feOffset") {
            ox = (int)std::lround(std::atof(prim.attribute("dx").as_string()));
            oy = (int)std::lround(std::atof(prim.attribute("dy").as_string()));
        }
    }
    if (ox || oy) {
        std::vector<unsigned char> sh(px.size(), 0);
        for (int y=0;y<H;++y) for (int x=0;x<W;++x) { int xx=x+ox, yy=y+oy; if(xx<0||xx>=W||yy<0||yy>=H)continue; std::memcpy(sh.data()+((size_t)yy*W+xx)*4, px.data()+((size_t)y*W+x)*4, 4); }
        px.swap(sh);
    }
    Gdiplus::Bitmap out(W,H,PixelFormat32bppARGB);
    Gdiplus::Rect orc(0,0,W,H); Gdiplus::BitmapData obd{};
    if (out.LockBits(&orc, Gdiplus::ImageLockModeWrite, PixelFormat32bppARGB, &obd) == Gdiplus::Ok) {
        for (int y=0;y<H;++y) std::memcpy((unsigned char*)obd.Scan0+(size_t)y*obd.Stride, px.data()+(size_t)y*W*4, (size_t)W*4);
        out.UnlockBits(&obd);
    }
    CLSID png; if (!gdi_encoder_clsid(L"image/png", &png)) return "";
    IStream* stream=nullptr; if (FAILED(CreateStreamOnHGlobal(nullptr, TRUE, &stream))) return "";
    if (out.Save(stream, &png, nullptr) != Gdiplus::Ok) { stream->Release(); return ""; }
    STATSTG st{}; stream->Stat(&st, STATFLAG_NONAME);
    HGLOBAL hmem=nullptr; GetHGlobalFromStream(stream, &hmem); void* data=GlobalLock(hmem);
    std::string res = b64enc((const unsigned char*)data, (size_t)st.cbSize.QuadPart);
    GlobalUnlock(hmem); stream->Release();
    return res;
}

static std::map<std::string,std::string> gdi_patterns(pugi::xml_node root) {
    g_gdi_root = root;
    std::map<std::string,std::string> out;
    for (pugi::xpath_node xn : root.select_nodes("//pattern")) {
        auto el = xn.node(); std::string id = el.attribute("id").as_string();
        if (id.empty()) continue;
        int w=0,h=0; std::string units;
        std::string data = gdi_pattern_png(el, w, h, units);
        if (data.empty()) continue;
        out[id] = data + "|" + std::to_string(w) + "|" + std::to_string(h) + "|" + units;
    }
    return out;
}

struct GdiSession {
    ULONG_PTR token = 0;
    GdiSession() { Gdiplus::GdiplusStartupInput in; Gdiplus::GdiplusStartup(&token, &in, nullptr); }
    ~GdiSession() { if (token) Gdiplus::GdiplusShutdown(token); }
};

static std::string plan_from_svg(const std::string& input) {
    GdiSession gdi;
    pugi::xml_document doc;
    if (!doc.load_buffer(input.c_str(), input.size())) return "ERR: XML parse failed";
    pugi::xml_node root = doc.document_element();
    load_icc_profiles(root);
    int W=1200,H=900;
    auto vb = nums(root.attribute("viewBox").as_string());
    if (vb.size()>=4) { W=(int)vb[2]; H=(int)vb[3]; }
    else { if(root.attribute("width"))W=root.attribute("width").as_int(); if(root.attribute("height"))H=root.attribute("height").as_int(); }
    std::vector<std::string> names, blends;
    std::map<std::string,int> idx;
    std::vector<std::string> strokes;
    auto css=load_css(root);
    auto grads=load_grads(root);
    auto clips=load_clips(root);
    auto patterns=gdi_patterns(root);
    std::map<std::string,pugi::xml_node> ids;
    std::function<void(pugi::xml_node)> collect_ids=[&](pugi::xml_node n){ for(pugi::xml_node q=n.first_child();q;q=q.next_sibling()){ std::string id=q.attribute("id").as_string(); if(!id.empty())ids[id]=q; collect_ids(q);} };
    collect_ids(root);
    auto at_of=[&](pugi::xml_node ch,const char*k){ std::string v=css_attr(ch,css,k); if(std::string(k)=="fill"||std::string(k)=="stroke")v=resolve_paint(v,grads); return v; };
    auto emit=[&](pugi::xml_node ch,const std::string& layer,const std::vector<double>& xs,const std::vector<double>& ys,const std::vector<double>& ps, const std::map<std::string,std::string>& inh){
        auto av=[&](const char* k){ std::string v=at_of(ch,k); if(v.empty()){auto it=inh.find(k); if(it!=inh.end())v=it->second;} return v; };
        std::string ct=av("data-color");
        auto col=ct.empty()?color_from(av("stroke").empty()?av("fill"):av("stroke")):color_from(ct);
        std::string sj="{\"layer\":\""; jesc_to(sj,layer);
        std::string preset=av("data-preset");
        bool shape=std::string(ch.name())=="circle"||std::string(ch.name())=="ellipse"||std::string(ch.name())=="rect"||std::string(ch.name())=="polygon"||std::string(ch.name())=="polyline";
        bool fillOnly=shape && !av("fill").empty() && av("fill")!="none" && av("stroke").empty();
        if(preset.empty())preset=fillOnly?"fill":"round_soft";
        sj += "\",\"preset\":\""; jesc_to(sj,preset);
        double sz=av("data-size").empty()?(shape?(av("stroke-width").empty()?1.0:std::atof(av("stroke-width").c_str())):std::atof(av("stroke-width").c_str())):std::atof(av("data-size").c_str());
        sj += "\",\"size\":"; sj += fmt(sz);
        sj += ",\"opacity\":"; sj += fmt(av("data-opacity").empty()?1.0:std::atof(av("data-opacity").c_str()));
        sj += ",\"hardness\":"; sj += fmt(av("data-hardness").empty()?0.7:std::atof(av("data-hardness").c_str()));
        sj += ",\"color\":["; sj += fmt(col[0]); sj += ","; sj += fmt(col[1]); sj += ","; sj += fmt(col[2]); sj += "],\"points\":[";
        for(size_t i=0;i<xs.size();++i){ if(i)sj += ","; sj += "["; sj += fmt(xs[i]); sj += ","; sj += fmt(ys[i]); sj += ","; sj += fmt(i<ps.size()?ps[i]:1.0); sj += "]"; }
        sj += "]";
        if(!av("data-tag").empty()){ sj += ",\"tag\":\""; jesc_to(sj,av("data-tag")); sj += "\""; }
        std::string rawFill=css_attr(ch,css,"fill"), rawStroke=css_attr(ch,css,"stroke");
        if(rawFill.rfind("url(#",0)==0){ std::string id=rawFill.substr(5,rawFill.find(')',5)-5); auto gi=g_grad_paint.find(id); if(gi!=g_grad_paint.end()) sj += ",\"paint\":"+gi->second; }
        else if(rawStroke.rfind("url(#",0)==0){ std::string id=rawStroke.substr(5,rawStroke.find(')',5)-5); auto gi=g_grad_paint.find(id); if(gi!=g_grad_paint.end()) sj += ",\"paint\":"+gi->second; }
        sj += "}";
        strokes.push_back(sj);
    };
    auto emit_any=[&](pugi::xml_node ch,const std::string& layer,const std::vector<double>& xs,const std::vector<double>& ys,const std::vector<double>& ps,const std::map<std::string,std::string>& inh){
        auto av=[&](const char*k){std::string v=at_of(ch,k); if(v.empty()){auto q=inh.find(k);if(q!=inh.end())v=q->second;}return v;};
        std::string ck=clip_url(at_of(ch,"clip-path")); if(ck.empty())ck=clip_url(at_of(ch,"mask"));
        auto it=clips.find(ck);
        if(ck.empty()||it==clips.end()){emit(ch,layer,xs,ys,ps,inh);return;}
        std::string fill=at_of(ch,"fill"), stroke=at_of(ch,"stroke");
        bool filled=at_of(ch,"data-preset")=="fill" || (!fill.empty()&&fill!="none"&&stroke.empty());
        if(it->second.evenodd){
            Clipper2Lib::PathsD subj, cp;
            if(filled){ Clipper2Lib::PathD q; for(size_t i=0;i<xs.size();++i)q.push_back(Clipper2Lib::PointD(xs[i],ys[i])); if(q.size()>=3)subj.push_back(q);
                for(const auto& poly:it->second.polys){Clipper2Lib::PathD z; for(auto&v:poly)z.push_back(Clipper2Lib::PointD(v.first,v.second)); if(z.size()>=3)cp.push_back(z);}
                auto res=Clipper2Lib::Intersect(subj,cp,Clipper2Lib::FillRule::EvenOdd);
                auto area2=[&](const Clipper2Lib::PathD& q){ return std::fabs(Clipper2Lib::Area(q)); };
                std::vector<int> par(res.size(), -1);
                for (size_t i=0;i<res.size();++i) for (size_t j=0;j<res.size();++j) {
                    if (i==j || area2(res[j])<=area2(res[i])+1e-9) continue;
                    if (Clipper2Lib::PointInPolygon(res[i][0], res[j]) != Clipper2Lib::PointInPolygonResult::IsOutside) { par[i]=(int)j; break; }
                }
                for (size_t i=0;i<res.size();++i) {
                    if (par[i] != -1 || res[i].size()<3) continue;
                    std::vector<double> a,b,p(res[i].size(),1.0);
                    for(auto&v:res[i]){a.push_back(v.x);b.push_back(v.y);}
                    emit(ch,layer,a,b,p,inh);
                    std::string extra="";
                    for (size_t j=0;j<res.size();++j) if (par[j]==(int)i && res[j].size()>=3) {
                        if (!extra.empty()) extra += ",";
                        extra += "[";
                        for (size_t k=0;k<res[j].size();++k) { if(k)extra+=","; extra += "["+fmt(res[j][k].x)+","+fmt(res[j][k].y)+"]"; }
                        extra += "]";
                    }
                    if (!extra.empty() && !strokes.empty()) {
                        std::string &sj=strokes.back();
                        if (!sj.empty() && sj.back()=='}') sj.insert(sj.size()-1, ",\"holes\":["+extra+"],\"fill_rule\":\"evenodd\"");
                    }
                }
                return;
            }
            auto inside=[&](double x,double y){bool c=false; for(const auto& poly:it->second.polys){for(size_t i=0;i<poly.size();++i){auto A=poly[i],B=poly[(i+1)%poly.size()]; if(((A.second>y)!=(B.second>y)) && x < (B.first-A.first)*(y-A.second)/(B.second-A.second)+A.first) c=!c;}} return c;};
            for(size_t i=1;i<xs.size();++i){
                std::vector<double> ts={0,1}; double dx=xs[i]-xs[i-1],dy=ys[i]-ys[i-1];
                for(const auto& poly:it->second.polys){for(size_t j=0;j<poly.size();++j){auto A=poly[j],B=poly[(j+1)%poly.size()];double ex=B.first-A.first,ey=B.second-A.second,den=ex*dy-ey*dx; if(std::fabs(den)<1e-12)continue; double t=(ex*(A.second-ys[i-1])-ey*(A.first-xs[i-1]))/den; if(t>1e-9&&t<1-1e-9)ts.push_back(t);}}
                std::sort(ts.begin(),ts.end());
                for(size_t j=1;j<ts.size();++j){double tm=(ts[j-1]+ts[j])*0.5; if(inside(xs[i-1]+dx*tm,ys[i-1]+dy*tm)){double x0=xs[i-1]+dx*ts[j-1],y0=ys[i-1]+dy*ts[j-1],x1=xs[i-1]+dx*ts[j],y1=ys[i-1]+dy*ts[j]; emit(ch,layer,{x0,x1},{y0,y1},{1.0,1.0},inh);}}
            }
            return;
        }
        for(const auto& poly:it->second.polys){
            std::vector<Poly> tris; if(is_convex(poly)) tris.push_back(poly); else { tris=triangulate(poly); if(tris.empty())tris.push_back(poly); }
            for(const auto& tri:tris){
                if(filled){
                    Poly q; for(size_t i=0;i<xs.size();++i)q.push_back({xs[i],ys[i]});
                    Poly r=clip_poly(q,tri); if(r.size()>=3){std::vector<double> a,b,p(r.size(),1.0);for(auto&v:r){a.push_back(v.first);b.push_back(v.second);} emit(ch,layer,a,b,p,inh);}
                } else for(size_t i=1;i<xs.size();++i){std::pair<double,double> a,b; if(clip_seg({xs[i-1],ys[i-1]},{xs[i],ys[i]},tri,a,b)){emit(ch,layer,{a.first,b.first},{a.second,b.second},{1.0,1.0},inh);}}
            }
        }
    };
    std::function<void(pugi::xml_node,std::string,Mat,std::map<std::string,std::string>)> walk = [&](pugi::xml_node node, std::string inherit, Mat parent, std::map<std::string,std::string> inh) {
        for (pugi::xml_node ch=node.first_child(); ch; ch=ch.next_sibling()) {
            std::string tag=ch.name();
            if(tag=="defs"||tag=="style"||tag=="clipPath"||tag=="mask"||tag=="linearGradient"||tag=="radialGradient"||tag=="symbol") continue;
            auto at=[&](const char* k){ std::string v=css_attr(ch,css,k); if(v.empty()){auto ii=inh.find(k); if(ii!=inh.end())v=ii->second;} if(std::string(k)=="fill"||std::string(k)=="stroke")v=resolve_paint(v,grads); return v; };
            std::string fref = at("filter");
            if (!fref.empty()) {
                int fw=0, fh=0;
                std::string data = gdi_filter_png(ch, root, W, H, fw, fh);
                if (!data.empty()) {
                    std::string layer=at("data-layer"); if(layer.empty())layer=inherit.empty()?"layer":inherit;
                    add_layer(names,blends,idx,layer,"");
                    std::string sj="{\"layer\":\""; jesc_to(sj,layer);
                    sj += "\",\"preset\":\"image\",\"x\":0,\"y\":0,\"width\":"; sj += std::to_string(fw);
                    sj += ",\"height\":"; sj += std::to_string(fh); sj += ",\"opacity\":1,\"image\":{\"encoding\":\"png-base64\",\"mime\":\"image/png\",\"data\":\"";
                    jesc_to(sj,data); sj += "\"}}";
                    strokes.push_back(sj); continue;
                }
            }
            if (tag=="g") {
                std::string n=at("data-name"); if(n.empty())n=at("id"); if(n.empty())n=inherit.empty()?"layer":inherit;
                add_layer(names,blends,idx,n,at("data-blend"));
                Mat m=mat_mul(parent,parse_transform(at("transform")));
                std::map<std::string,std::string> my=inh;
                for(const char* key: {"fill","stroke","stroke-width","opacity","fill-opacity"}){std::string v=at(key); if(!v.empty())my[key]=v;}
                if(!at("data-stroke").empty() || (!at("data-center").empty() && at("data-name").empty())){
                    std::string layer=at("data-layer"); if(layer.empty())layer=inherit.empty()?"layer":inherit;
                    add_layer(names,blends,idx,layer,"");
                    std::vector<double> xs,ys,ps;
                    parse_center(at("data-center"),xs,ys,ps);
                    for(size_t i=0;i<xs.size();++i) mat_apply(m,xs[i],ys[i]);
                    if(!xs.empty()) emit_any(ch,layer,xs,ys,ps,inh);
                    continue;
                }
                walk(ch,n,m,my);
            } else if (tag=="text") {
                std::string txt=ch.text().get();
                std::string layer=at("data-layer"); if(layer.empty())layer=inherit.empty()?"layer":inherit;
                add_layer(names,blends,idx,layer,"");
                double x=std::atof(at("x").c_str()), y=std::atof(at("y").c_str());
                double fs=at("font-size").empty()?16.0:std::atof(at("font-size").c_str());
                double op=at("opacity").empty()?1.0:std::atof(at("opacity").c_str());
                std::string fam=at("font-family"); if(fam.empty())fam="Arial";
                std::string anch=at("text-anchor"); if(anch.empty())anch="start";
                std::string fill=at("fill"); if(fill.empty())fill="#000";
                std::vector<std::string> runs;
                if (!txt.empty()) {
                    auto c0=color_from(fill);
                    std::string r="{\"text\":\""; jesc_to(r,txt); r+="\",\"font_family\":\""; jesc_to(r,fam);
                    r+="\",\"font_size\":"+fmt(fs)+",\"color\":["+fmt(c0[0])+","+fmt(c0[1])+","+fmt(c0[2])+"]}";
                    runs.push_back(r);
                }
                for (pugi::xml_node tch : ch.children()) {
                    if (std::string(tch.name()) != "tspan" || !tch.text()) continue;
                    std::string rf = tch.attribute("fill").as_string(); if (rf.empty()) rf = fill;
                    double rfs = tch.attribute("font-size") ? std::atof(tch.attribute("font-size").as_string()) : fs;
                    std::string rfam = tch.attribute("font-family").as_string(); if (rfam.empty()) rfam = fam;
                    auto c0=color_from(rf);
                    std::string r="{\"text\":\""; jesc_to(r,tch.text().get()); r+="\",\"font_family\":\""; jesc_to(r,rfam);
                    r+="\",\"font_size\":"+fmt(rfs)+",\"color\":["+fmt(c0[0])+","+fmt(c0[1])+","+fmt(c0[2])+"]}";
                    runs.push_back(r);
                }
                if (txt.empty() && runs.empty()) continue;
                std::string sj="{\"layer\":\""; jesc_to(sj,layer);
                sj += "\",\"preset\":\"text\",\"text\":\""; jesc_to(sj,txt);
                sj += "\",\"x\":"; sj += fmt(x); sj += ",\"y\":"; sj += fmt(y);
                sj += ",\"font_family\":\""; jesc_to(sj,fam); sj += "\",\"font_size\":"; sj += fmt(fs);
                sj += ",\"text_anchor\":\""; jesc_to(sj,anch); sj += "\",\"opacity\":"; sj += fmt(op);
                auto col=color_from(fill);
                sj += ",\"color\":["; sj += fmt(col[0]); sj += ","; sj += fmt(col[1]); sj += ","; sj += fmt(col[2])+"]";
                if (!runs.empty()) { sj += ",\"runs\":["; for(size_t ri=0;ri<runs.size();++ri){if(ri)sj+=",";sj+=runs[ri];} sj += "]"; }
                Mat tm = mat_mul(parent, parse_transform(at("transform")));
                bool tident = (std::fabs(tm.a-1)<1e-9 && std::fabs(tm.b)<1e-9 && std::fabs(tm.c)<1e-9 &&
                               std::fabs(tm.d-1)<1e-9 && std::fabs(tm.e)<1e-9 && std::fabs(tm.f)<1e-9);
                if (!tident) sj += ",\"transform\":["+fmt(tm.a)+","+fmt(tm.b)+","+fmt(tm.c)+","+fmt(tm.d)+","+fmt(tm.e)+","+fmt(tm.f)+"]";
                sj += "}";
                strokes.push_back(sj);
            } else if (tag=="image") {
                std::string href=at("href"); if(href.empty())href=std::string(ch.attribute("{http://www.w3.org/1999/xlink}href").as_string());
                if(href.empty())continue;
                std::string mime="image/png", data;
                if(href.rfind("data:",0)==0){
                    size_t comma=href.find(',');
                    if(comma==std::string::npos)continue;
                    std::string head=href.substr(5,comma-5);
                    size_t semi=head.find(';');
                    mime=head.substr(0,semi==std::string::npos?head.size():semi);
                    data=href.substr(comma+1);
                } else {
                    if(g_base_dir.empty())continue;
                    std::string path=g_base_dir+"/"+href;
                    std::ifstream f(path,std::ios::binary); if(!f)continue;
                    std::string raw((std::istreambuf_iterator<char>(f)),std::istreambuf_iterator<char>());
                    data=b64enc((const unsigned char*)raw.data(),raw.size());
                    if(path.size()>4&&(path.substr(path.size()-4)==".jpg"||path.size()>5&&path.substr(path.size()-5)==".jpeg"))mime="image/jpeg";
                }
                std::string layer=at("data-layer"); if(layer.empty())layer=inherit.empty()?"layer":inherit;
                add_layer(names,blends,idx,layer,"");
                double x=std::atof(at("x").c_str()), y=std::atof(at("y").c_str());
                double w=std::atof(at("width").c_str()), h=std::atof(at("height").c_str());
                double op=at("opacity").empty()?1.0:std::atof(at("opacity").c_str());
                Mat imat = mat_mul(parent, parse_transform(at("transform")));
                bool iident = (std::fabs(imat.a-1)<1e-9 && std::fabs(imat.b)<1e-9 && std::fabs(imat.c)<1e-9 &&
                               std::fabs(imat.d-1)<1e-9 && std::fabs(imat.e)<1e-9 && std::fabs(imat.f)<1e-9);
                std::string sj="{\"layer\":\""; jesc_to(sj,layer);
                sj += "\",\"preset\":\"image\",\"x\":"; sj += fmt(x); sj += ",\"y\":"; sj += fmt(y);
                sj += ",\"width\":"; sj += fmt(w); sj += ",\"height\":"; sj += fmt(h);
                sj += ",\"opacity\":"; sj += fmt(op);
                sj += ",\"image\":{\"encoding\":\"png-base64\",\"mime\":\""; jesc_to(sj,mime);
                sj += "\",\"data\":\""; jesc_to(sj,data); sj += "\"}";
                if (!iident) {
                    sj += ",\"transform\":[" + fmt(imat.a) + "," + fmt(imat.b) + "," + fmt(imat.c) + "," + fmt(imat.d) + "," + fmt(imat.e) + "," + fmt(imat.f) + "]";
                }
                sj += "}";
                strokes.push_back(sj);
            } else if (tag=="path") {
                std::string layer=at("data-layer"); if(layer.empty())layer=inherit.empty()?"layer":inherit;
                add_layer(names,blends,idx,layer,"");
                std::vector<double> xs,ys,ps;
                if(!at("data-center").empty()){
                    parse_center(at("data-center"),xs,ys,ps);
                    if(xs.empty())continue;
                    Mat m=mat_mul(parent,parse_transform(at("transform")));
                    for(size_t i=0;i<xs.size();++i) mat_apply(m,xs[i],ys[i]);
                    emit_any(ch,layer,xs,ys,ps,inh);
                } else {
                    Mat m=mat_mul(parent,parse_transform(at("transform")));
                    for(const auto& part:split_d_subpaths(at("d"))){
                        xs.clear();ys.clear();parse_d(part,xs,ys); if(xs.empty())continue;
                        ps.assign(xs.size(),1.0);
                        for(size_t i=0;i<xs.size();++i) mat_apply(m,xs[i],ys[i]);
                        emit_any(ch,layer,xs,ys,ps,inh);
                    }
                }
            } else if (tag=="use") {
                std::string href=at("href"); if(href.empty())href=std::string(ch.attribute("{http://www.w3.org/1999/xlink}href").as_string());
                if(href.size()>1 && href[0]=='#'){
                    auto it=ids.find(href.substr(1));
                    if(it!=ids.end()){
                        Mat m=mat_mul(parent,parse_transform(at("transform")));
                        m=mat_mul(m,Mat{1,0,0,1,std::atof(at("x").c_str()),std::atof(at("y").c_str())});
                        pugi::xml_document tmp; auto cp=tmp.append_copy(it->second); if(std::string(cp.name())=="symbol")cp.set_name("g"); walk(tmp,inherit,m,inh);
                    }
                }
            } else if (tag=="circle"||tag=="ellipse"||tag=="rect"||tag=="polygon"||tag=="polyline") {
                std::string layer=at("data-layer"); if(layer.empty())layer=inherit.empty()?"layer":inherit;
                add_layer(names,blends,idx,layer,"");
                std::vector<double> xs,ys; shape_points(tag,ch,css,xs,ys);
                if(xs.empty())continue;
                Mat m=mat_mul(parent,parse_transform(at("transform")));
                for(size_t i=0;i<xs.size();++i) mat_apply(m,xs[i],ys[i]);
                std::vector<double> ps(xs.size(),1.0);
                std::string fv=at("fill");
                auto pm = fv.rfind("url(#",0)==0 ? patterns.find(fv.substr(5, fv.find(')',5)-5)) : patterns.end();
                if (pm != patterns.end()) {
                    auto parts = pm->second; size_t p1=parts.find('|');
                    std::string pdata=parts.substr(0,p1), rest=parts.substr(p1+1);
                    size_t p2=rest.find('|'); std::string pw=rest.substr(0,p2), rest2=rest.substr(p2+1);
                    size_t p3=rest2.find('|'); std::string ph=rest2.substr(0,p3), punits=rest2.substr(p3+1);
                    std::string sj="{\"layer\":\""; jesc_to(sj,layer);
                    sj += "\",\"preset\":\"fill\",\"opacity\":" + std::string("1") + ",\"points\":[";
                    for(size_t i=0;i<xs.size();++i){ if(i)sj+=","; sj+="["+fmt(xs[i])+","+fmt(ys[i])+",1]"; }
                    sj += "],\"pattern\":{\"encoding\":\"png-base64\",\"data\":\""; jesc_to(sj,pdata);
                    sj += "\",\"width\":"+pw+",\"height\":"+ph+",\"units\":\""; jesc_to(sj,punits); sj += "\"}}";
                    strokes.push_back(sj); continue;
                }
                emit_any(ch,layer,xs,ys,ps,inh);
            } else walk(ch,inherit,parent,inh);
        }
    };
    walk(root,"",Mat(),std::map<std::string,std::string>());
    std::string out="{\"canvas\":["; out+=std::to_string(W); out+=","; out+=std::to_string(H); out+="],\"layers\":[";
    for(size_t i=0;i<names.size();++i){ if(i)out+=","; out+="{\"name\":\""; jesc_to(out,names[i]); out+="\",\"blend\":\""; jesc_to(out,blends[i]); out+="\"}"; }
    out += "],\"strokes\":[";
    for(size_t i=0;i<strokes.size();++i){ if(i)out+=","; out+=strokes[i]; }
    out += "],\"count\":"; out += std::to_string(strokes.size()); out += "}";
    return out;
}

static char* dupstr(const std::string& s) {
    char* p=(char*)std::malloc(s.size()+1); if(p)std::memcpy(p,s.c_str(),s.size()+1); return p;
}
EXPORT char* psvg_clip_polygons(const double* subject,int sn,const int* soff,
                                const double* clip,int cn,const int* coff,int evenodd,int* out_len){
    try{
        Clipper2Lib::PathsD subj, clips;
        for(int i=0;i<sn;++i){ Clipper2Lib::PathD p; for(int j=soff[i];j<soff[i+1];++j)p.push_back(Clipper2Lib::PointD(subject[2*j],subject[2*j+1])); if(p.size()>=3)subj.push_back(p); }
        for(int i=0;i<cn;++i){ Clipper2Lib::PathD p; for(int j=coff[i];j<coff[i+1];++j)p.push_back(Clipper2Lib::PointD(clip[2*j],clip[2*j+1])); if(p.size()>=3)clips.push_back(p); }
        auto res=Clipper2Lib::Intersect(subj,clips,evenodd?Clipper2Lib::FillRule::EvenOdd:Clipper2Lib::FillRule::NonZero);
        std::string out="[";
        for(size_t i=0;i<res.size();++i){ if(i)out+=","; out+="["; for(size_t j=0;j<res[i].size();++j){ if(j)out+=","; out+="["+fmt(res[i][j].x)+","+fmt(res[i][j].y)+"]"; } out+="]"; }
        out+="]";
        *out_len=(int)out.size(); char* p=(char*)std::malloc(out.size()); if(p)std::memcpy(p,out.data(),out.size()); return p;
    }catch(const std::exception&e){ std::string s=std::string("ERR:")+e.what(); *out_len=(int)s.size(); return dupstr(s); }
}

EXPORT char* psvg_plan_to_svg(const char* in,int n){ try{return dupstr(svg_from_plan(std::string(in,n)));}catch(const std::exception&e){return dupstr(std::string("ERR:")+e.what());} }
EXPORT char* psvg_svg_to_plan(const char* in,int n){ try{return dupstr(plan_from_svg(std::string(in,n)));}catch(const std::exception&e){return dupstr(std::string("ERR:")+e.what());} }
static std::string svg_to_bin(const std::string&);
EXPORT char* psvg_svg_to_plan_base(const char* in,int n,const char* base){ try{ g_base_dir=base?base:""; auto r=plan_from_svg(std::string(in,n)); g_base_dir.clear(); return dupstr(r);}catch(const std::exception&e){g_base_dir.clear();return dupstr(std::string("ERR:")+e.what());} }
EXPORT char* psvg_svg_to_bin_base(const char* in,int n,const char* base,int* out_len){ try{ g_base_dir=base?base:""; std::string s=svg_to_bin(std::string(in,n)); g_base_dir.clear(); *out_len=(int)s.size(); char*p=(char*)std::malloc(s.size()); if(p)std::memcpy(p,s.data(),s.size()); return p;}catch(const std::exception&e){g_base_dir.clear(); return dupstr(std::string("ERR:")+e.what());} }
static std::string read_all(const char* path) {
    std::ifstream f(path, std::ios::binary);
    if (!f) throw std::runtime_error("open failed");
    std::ostringstream ss; ss << f.rdbuf(); return ss.str();
}
static void write_all(const char* path, const std::string& s) {
    std::ofstream f(path, std::ios::binary);
    if (!f) throw std::runtime_error("write failed");
    f.write(s.data(), (std::streamsize)s.size());
}
static std::string plan_json_to_bin(const std::string&);
static std::string bin_to_plan_json(const unsigned char*, int);
static std::string scan_from_svg(const std::string&);
static std::string bin_from_svg(const std::string&);

EXPORT char* psvg_plan_file_to_bin(const char* plan_path, const char* bin_path) {
    try {
        std::string out = plan_json_to_bin(read_all(plan_path));
        write_all(bin_path, out);
        return dupstr(std::string("{\"ok\":true,\"bytes\":") + std::to_string(out.size()) + "}");
    } catch (const std::exception& e) { return dupstr(std::string("ERR:") + e.what()); }
}
EXPORT char* psvg_bin_file_to_plan(const char* bin_path, const char* plan_path) {
    try {
        std::string input = read_all(bin_path);
        std::string out = bin_to_plan_json((const unsigned char*)input.data(), (int)input.size());
        write_all(plan_path, out);
        return dupstr(std::string("{\"ok\":true,\"bytes\":") + std::to_string(out.size()) + "}");
    } catch (const std::exception& e) { return dupstr(std::string("ERR:") + e.what()); }
}
EXPORT char* psvg_plan_file_to_svg(const char* plan_path, const char* svg_path) {
    try {
        std::string out = svg_from_plan(read_all(plan_path));
        write_all(svg_path, out);
        return dupstr(std::string("{\"ok\":true,\"bytes\":") + std::to_string(out.size()) + "}");
    } catch (const std::exception& e) { return dupstr(std::string("ERR:") + e.what()); }
}
EXPORT char* psvg_svg_file_to_plan(const char* svg_path, const char* plan_path) {
    try {
        std::string sp(svg_path);
        size_t slash=sp.find_last_of("\\/");
        g_base_dir = slash==std::string::npos ? "" : sp.substr(0,slash);
        std::string input = read_all(svg_path);
        std::string out = plan_from_svg(input);
        write_all(plan_path, out);
        return dupstr(std::string("{\"ok\":true,\"bytes\":") + std::to_string(out.size()) + "}");
    } catch (const std::exception& e) { return dupstr(std::string("ERR:") + e.what()); }
}

struct BR {
    const unsigned char* p; int n, i;
    BR(const unsigned char* d, int len) : p(d), n(len), i(0) {}
    void need(int k) { if (i + k > n) throw std::runtime_error("short binary"); }
    unsigned char u8() { need(1); return p[i++]; }
    uint16_t u16() { need(2); uint16_t v; std::memcpy(&v,p+i,2); i+=2; return v; }
    uint32_t u32() { need(4); uint32_t v; std::memcpy(&v,p+i,4); i+=4; return v; }
    int32_t i32() { need(4); int32_t v; std::memcpy(&v,p+i,4); i+=4; return v; }
    float f32() { need(4); float v; std::memcpy(&v,p+i,4); i+=4; return v; }
    std::string str16() { int n=u16(); need(n); std::string s((const char*)p+i,n); i+=n; return s; }
};
static void wr8(std::vector<unsigned char>& v, unsigned char x){ v.push_back(x); }
static void wr16(std::vector<unsigned char>& v, uint16_t x){ const unsigned char* p=(const unsigned char*)&x; v.insert(v.end(),p,p+2); }
static void wr32(std::vector<unsigned char>& v, uint32_t x){ const unsigned char* p=(const unsigned char*)&x; v.insert(v.end(),p,p+4); }
static void wrf(std::vector<unsigned char>& v, float x){ const unsigned char* p=(const unsigned char*)&x; v.insert(v.end(),p,p+4); }
static void wrstr(std::vector<unsigned char>& v, const std::string& s){ wr16(v,(uint16_t)s.size()); v.insert(v.end(),s.begin(),s.end()); }

struct BStroke { int layer; std::string preset, tag; float size, opacity, hardness, r,g,b; std::vector<float> pts; };

static std::string bin_to_svg(const unsigned char* data, int len) {
    BR b(data, len);
    if (b.u32() != 0x42565350) throw std::runtime_error("bad binary magic");
    { uint32_t v=b.u32(); if (v != 1 && v != 2) throw std::runtime_error("bad binary version"); }
    int w=b.i32(), h=b.i32(), lc=b.i32(), sc=b.i32();
    std::vector<std::string> names(lc), blends(lc);
    for (int i=0;i<lc;++i){ blends[i]=b.str16(); names[i]=b.str16(); }
    std::vector<BStroke> strokes; strokes.reserve(sc);
    for (int k=0;k<sc;++k) {
        BStroke s; s.layer=b.u16(); s.preset=b.str16();
        s.size=b.f32(); s.opacity=b.f32(); s.hardness=b.f32();
        s.r=b.f32(); s.g=b.f32(); s.b=b.f32();
        int pc=b.u16(); s.pts.reserve(pc*3);
        for(int j=0;j<pc;++j){ s.pts.push_back(b.f32()); s.pts.push_back(b.f32()); s.pts.push_back(b.f32()); }
        s.tag=b.str16(); strokes.push_back(std::move(s));
    }
    std::string out; out.reserve(16*1024*1024);
    out += "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"" + std::to_string(w) + "\" height=\"" + std::to_string(h) + "\" viewBox=\"0 0 " + std::to_string(w) + " " + std::to_string(h) + "\" data-bridge=\"plan_svg\" shape-rendering=\"geometricPrecision\">\n";
    for (int li=0; li<lc; ++li) {
        out += "<g id=\"L" + std::to_string(li) + "\" data-name=\""; esc_to(out,names[li]);
        out += "\" data-blend=\""; esc_to(out,blends[li]);
        out += "\" style=\"isolation:isolate;mix-blend-mode:"; esc_to(out,blends[li]); out += "\">\n";
        for (const auto& s : strokes) if (s.layer == li) {
            std::string d; bool first=true;
            for (size_t j=0;j+2<s.pts.size();j+=3){ d += first?"M ":" L "; d += fmt(s.pts[j])+" "+fmt(s.pts[j+1]); first=false; }
            if (s.preset == "fill") {
                char hf[8]; std::snprintf(hf,sizeof(hf),"%02x%02x%02x",(int)std::lround(s.r),(int)std::lround(s.g),(int)std::lround(s.b));
                out += "<path d=\"" + d + " Z\" fill=\"#"; out += hf; out += "\" fill-opacity=\"" + fmt(s.opacity) + "\" fill-rule=\"nonzero\" data-layer=\""; esc_to(out,names[li]);
                out += "\" data-preset=\"fill\" data-color=\"" + fmt(s.r) + "," + fmt(s.g) + "," + fmt(s.b) + "\"";
                if(!s.tag.empty()){ out += " data-tag=\""; esc_to(out,s.tag); out += "\""; }
                out += "/>\n"; continue;
            }
            out += "<path d=\"" + d + "\" fill=\"none\" stroke=\"#";
            char hx[8]; std::snprintf(hx,sizeof(hx),"%02x%02x%02x",(int)std::lround(s.r),(int)std::lround(s.g),(int)std::lround(s.b)); out += hx;
            out += "\" stroke-width=\"" + fmt(s.size) + "\" stroke-opacity=\"" + fmt(s.opacity);
            out += "\" stroke-linecap=\"" + std::string((s.preset=="flat"||s.preset=="bristle")?"butt":"round") + "\" stroke-linejoin=\"round\" data-layer=\""; esc_to(out,names[li]);
            out += "\" data-preset=\""; esc_to(out,s.preset); out += "\" data-size=\""+fmt(s.size)+"\" data-opacity=\""+fmt(s.opacity)+"\" data-hardness=\""+fmt(s.hardness);
            out += "\" data-color=\""+fmt(s.r)+","+fmt(s.g)+","+fmt(s.b)+"\" data-p=\"";
            for(size_t j=0;j+2<s.pts.size();j+=3){ if(j)out+=","; out+=fmt(s.pts[j+2]); }
            out += "\"";
            if(!s.tag.empty()){ out+=" data-tag=\""; esc_to(out,s.tag); out+="\""; }
            out += "/>\n";
        }
        out += "</g>\n";
    }
    out += "</svg>\n";
    return out;
}


struct BinStroke { int layer; std::string preset, tag; float size, opacity, hardness, r, g, b; std::vector<float> pts; };



struct EP {
    std::vector<std::string> names, blends, stack;
    std::map<std::string,int> idx;
    std::vector<BinStroke> strokes;
    int W = 1200, H = 900;
};

struct Attrs {
    std::string name, id, blend, layer, d, p, color, stroke, size, sw, opacity, hardness, preset, tag, viewBox, width, height;
};

static void parse_attrs(const std::string& tag, Attrs& a) {
    size_t p = 1;
    while (p < tag.size() && !std::isspace((unsigned char)tag[p]) && tag[p] != '>' && tag[p] != '/') ++p;
    while (p < tag.size()) {
        while (p < tag.size() && std::isspace((unsigned char)tag[p])) ++p;
        if (p >= tag.size() || tag[p] == '>' || tag[p] == '/') break;
        size_t k0 = p;
        while (p < tag.size() && (std::isalnum((unsigned char)tag[p]) || tag[p] == '_' || tag[p] == '-' || tag[p] == ':')) ++p;
        if (p == k0) { ++p; continue; }
        std::string key = tag.substr(k0, p - k0);
        while (p < tag.size() && std::isspace((unsigned char)tag[p])) ++p;
        if (p >= tag.size() || tag[p] != '=') continue;
        ++p;
        while (p < tag.size() && std::isspace((unsigned char)tag[p])) ++p;
        std::string val;
        if (p < tag.size() && (tag[p] == '"' || tag[p] == '\'')) {
            char q = tag[p++]; size_t v0 = p;
            while (p < tag.size() && tag[p] != q) ++p;
            val = tag.substr(v0, p - v0);
            if (p < tag.size()) ++p;
        } else {
            size_t v0 = p;
            while (p < tag.size() && !std::isspace((unsigned char)tag[p]) && tag[p] != '>' && tag[p] != '/') ++p;
            val = tag.substr(v0, p - v0);
        }
        if (key == "data-name") a.name = val;
        else if (key == "id") a.id = val;
        else if (key == "data-blend") a.blend = val;
        else if (key == "data-layer") a.layer = val;
        else if (key == "d") a.d = val;
        else if (key == "data-p") a.p = val;
        else if (key == "data-color") a.color = val;
        else if (key == "stroke") a.stroke = val;
        else if (key == "data-size") a.size = val;
        else if (key == "stroke-width") a.sw = val;
        else if (key == "data-opacity") a.opacity = val;
        else if (key == "data-hardness") a.hardness = val;
        else if (key == "data-preset") a.preset = val;
        else if (key == "data-tag") a.tag = val;
        else if (key == "viewBox") a.viewBox = val;
        else if (key == "width") a.width = val;
        else if (key == "height") a.height = val;
    }
}

static std::string scan_from_svg(const std::string& input) {
    if (input.find("data-bridge=\"plan_svg\"") == std::string::npos)
        throw std::runtime_error("not bridge svg");
    EP e;
    size_t pos = 0;
    while ((pos = input.find('<', pos)) != std::string::npos) {
        if (input.compare(pos, 2, "</") == 0) {
            if (input.compare(pos, 4, "</g>") == 0 && !e.stack.empty()) e.stack.pop_back();
            pos += 2; continue;
        }
        size_t end = input.find('>', pos + 1);
        if (end == std::string::npos) break;
        std::string tag = input.substr(pos, end - pos + 1);
        Attrs at; parse_attrs(tag, at);
        if (tag.compare(0, 4, "<svg") == 0) {
            auto v = nums(at.viewBox);
            if (v.size() >= 4) { e.W = (int)v[2]; e.H = (int)v[3]; }
            else {
                if (!at.width.empty()) e.W = std::atoi(at.width.c_str());
                if (!at.height.empty()) e.H = std::atoi(at.height.c_str());
            }
        } else if (tag.compare(0, 3, "<g ") == 0 || tag.compare(0, 3, "<g>") == 0) {
            std::string n = at.name;
            if (n.empty()) n = at.id;
            if (n.empty()) n = e.stack.empty() ? "layer" : e.stack.back();
            add_layer(e.names, e.blends, e.idx, n, at.blend);
            e.stack.push_back(n);
        } else if (tag.compare(0, 6, "<path ") == 0) {
            std::string layer = at.layer;
            if (layer.empty()) layer = e.stack.empty() ? "layer" : e.stack.back();
            add_layer(e.names, e.blends, e.idx, layer, "");
            std::vector<double> xs, ys; parse_d(at.d, xs, ys);
            if (!xs.empty()) {
                auto ps = nums(at.p); if (ps.size() != xs.size()) ps.assign(xs.size(), 1.0);
                auto col = at.color.empty() ? color_from(at.stroke) : color_from(at.color);
                BinStroke st; st.layer = e.idx[layer];
                st.preset = at.preset.empty() ? "round_soft" : at.preset;
                st.size = (float)(at.size.empty() ? std::atof(at.sw.c_str()) : std::atof(at.size.c_str()));
                if (!(st.size > 0)) st.size = 8.0f;
                st.opacity = (float)(at.opacity.empty() ? 1.0 : std::atof(at.opacity.c_str()));
                st.hardness = (float)(at.hardness.empty() ? 0.7 : std::atof(at.hardness.c_str()));
                st.r=(float)col[0]; st.g=(float)col[1]; st.b=(float)col[2]; st.tag=at.tag;
                for (size_t i=0;i<xs.size();++i){ st.pts.push_back((float)xs[i]); st.pts.push_back((float)ys[i]); st.pts.push_back((float)ps[i]); }
                e.strokes.push_back(std::move(st));
            }
        }
        pos = end + 1;
    }
    std::vector<unsigned char> v; v.reserve(2*1024*1024);
    v.insert(v.end(), {'P','S','V','B'}); wr32(v,1);
    wr32(v,(uint32_t)e.W); wr32(v,(uint32_t)e.H); wr32(v,(uint32_t)e.names.size()); wr32(v,(uint32_t)e.strokes.size());
    for(size_t i=0;i<e.names.size();++i){ wrstr(v,e.blends[i]); wrstr(v,e.names[i]); }
    for(const auto& st:e.strokes){
        wr16(v,(uint16_t)st.layer); wrstr(v,st.preset);
        wrf(v,st.size); wrf(v,st.opacity); wrf(v,st.hardness); wrf(v,st.r); wrf(v,st.g); wrf(v,st.b);
        wr16(v,(uint16_t)(st.pts.size()/3));
        for(float x:st.pts)wrf(v,x);
        wrstr(v,st.tag);
    }
    return std::string((const char*)v.data(), v.size());
}
static std::string bin_from_svg(const std::string& input) {
    pugi::xml_document doc;
    if (!doc.load_buffer(input.c_str(), input.size())) throw std::runtime_error("XML parse failed");
    pugi::xml_node root = doc.document_element();
    load_icc_profiles(root);
    int W=1200,H=900;
    auto vb=nums(root.attribute("viewBox").as_string());
    if(vb.size()>=4){W=(int)vb[2];H=(int)vb[3];}
    else { if(root.attribute("width"))W=root.attribute("width").as_int(); if(root.attribute("height"))H=root.attribute("height").as_int(); }
    std::vector<std::string> names, blends;
    std::map<std::string,int> idx;
    std::vector<BinStroke> strokes;
    std::function<void(pugi::xml_node,std::string)> walk=[&](pugi::xml_node node,std::string inherit){
        for(pugi::xml_node ch=node.first_child(); ch; ch=ch.next_sibling()){
            std::string tag=ch.name();
            auto at=[&](const char*k){return std::string(ch.attribute(k).as_string());};
            if(tag=="g"){
                std::string n=at("data-name"); if(n.empty())n=at("id"); if(n.empty())n=inherit.empty()?"layer":inherit;
                add_layer(names,blends,idx,n,at("data-blend")); walk(ch,n);
            } else if(tag=="path"){
                std::string layer=at("data-layer"); if(layer.empty())layer=inherit.empty()?"layer":inherit;
                add_layer(names,blends,idx,layer,"");
                std::vector<double> xs,ys; parse_d(at("d"),xs,ys); if(xs.empty())continue;
                auto ps=nums(at("data-p")); if(ps.size()!=xs.size())ps.assign(xs.size(),1.0);
                std::string ct=at("data-color"); auto col=ct.empty()?color_from(at("stroke")):color_from(ct);
                BinStroke st; st.layer=idx[layer]; st.preset=at("data-preset").empty()?"round_soft":at("data-preset");
                st.size=(float)(at("data-size").empty()?std::atof(at("stroke-width").c_str()):std::atof(at("data-size").c_str()));
                if(!(st.size>0))st.size=8.0f;
                st.opacity=(float)(at("data-opacity").empty()?1.0:std::atof(at("data-opacity").c_str()));
                st.hardness=(float)(at("data-hardness").empty()?0.7:std::atof(at("data-hardness").c_str()));
                st.r=(float)col[0]; st.g=(float)col[1]; st.b=(float)col[2]; st.tag=at("data-tag");
                for(size_t i=0;i<xs.size();++i){ st.pts.push_back((float)xs[i]); st.pts.push_back((float)ys[i]); st.pts.push_back((float)ps[i]); }
                strokes.push_back(std::move(st));
            } else walk(ch,inherit);
        }
    };
    walk(root,"");
    std::vector<unsigned char> v; v.reserve(2*1024*1024);
    v.insert(v.end(), {'P','S','V','B'}); wr32(v,1);
    wr32(v,(uint32_t)W); wr32(v,(uint32_t)H); wr32(v,(uint32_t)names.size()); wr32(v,(uint32_t)strokes.size());
    for(size_t i=0;i<names.size();++i){ wrstr(v,blends[i]); wrstr(v,names[i]); }
    for(const auto& st:strokes){
        wr16(v,(uint16_t)st.layer); wrstr(v,st.preset);
        wrf(v,st.size); wrf(v,st.opacity); wrf(v,st.hardness); wrf(v,st.r); wrf(v,st.g); wrf(v,st.b);
        wr16(v,(uint16_t)(st.pts.size()/3));
        for(float x:st.pts)wrf(v,x);
        wrstr(v,st.tag);
    }
    return std::string((const char*)v.data(), v.size());
}

static std::string svg_to_bin(const std::string& input) {
    // Reuse the XML/JSON path, then encode to binary.
    std::string js = plan_from_svg(input);
    Document d; d.Parse(js.c_str(), js.size());
    if (d.HasParseError()) throw std::runtime_error("plan json parse failed");
    std::vector<unsigned char> v; v.reserve(2*1024*1024);
    v.insert(v.end(), {'P','S','V','B'});
    wr32(v, 1);
    int w=d["canvas"][0].GetInt(), h=d["canvas"][1].GetInt();
    const Value& ls=d["layers"]; const Value& ss=d["strokes"];
    std::map<std::string,int> idx;
    for(rapidjson::SizeType i=0;i<ls.Size();++i) idx[get_str(ls[i],"name","layer")]=(int)i;
    v.clear(); v.insert(v.end(), {'P','S','V','B'}); wr32(v,1); wr32(v,(uint32_t)w); wr32(v,(uint32_t)h); wr32(v,(uint32_t)ls.Size()); wr32(v,(uint32_t)ss.Size());
    for(const auto& l : ls.GetArray()){ wrstr(v,get_str(l,"blend","normal")); wrstr(v,get_str(l,"name","layer")); }
    for(const auto& s : ss.GetArray()){
        wr16(v,(uint16_t)idx[get_str(s,"layer","layer")]);
        wrstr(v,get_str(s,"preset","round_soft"));
        wrf(v,(float)get_num(s,"size",8.0)); wrf(v,(float)get_num(s,"opacity",1.0)); wrf(v,(float)get_num(s,"hardness",0.7));
        auto ci=s.FindMember("color"); double r=217,g=180,b=90;
        if(ci!=s.MemberEnd()&&ci->value.IsArray()&&ci->value.Size()>=3){r=ci->value[0].GetDouble();g=ci->value[1].GetDouble();b=ci->value[2].GetDouble();}
        wrf(v,(float)r); wrf(v,(float)g); wrf(v,(float)b);
        const Value& pts=s["points"]; wr16(v,(uint16_t)pts.Size());
        for(const auto& p : pts.GetArray()){ wrf(v,(float)p[0].GetDouble()); wrf(v,(float)p[1].GetDouble()); wrf(v,(float)(p.Size()>2?p[2].GetDouble():1.0)); }
        wrstr(v, s.HasMember("tag")?s["tag"].GetString():"");
    }
    return std::string((const char*)v.data(), v.size());
}
EXPORT char* psvg_bin_to_svg(const unsigned char* data, int n, int* out_len) {
    try { std::string s=bin_to_svg(data,n); *out_len=(int)s.size(); char* p=(char*)std::malloc(s.size()); if(p)std::memcpy(p,s.data(),s.size()); return p; }
    catch(const std::exception& e){ *out_len=(int)(std::string("ERR:")+e.what()).size(); return dupstr(std::string("ERR:")+e.what()); }
}
EXPORT char* psvg_svg_to_bin(const char* svg, int n, int* out_len) {
    try { std::string in(svg,n), s; if (in.find("data-bridge=\"plan_svg\"")!=std::string::npos && in.find("Gradient")==std::string::npos) { try { s=scan_from_svg(in); } catch (...) { s=bin_from_svg(in); } } else { s=svg_to_bin(in); } *out_len=(int)s.size(); char* p=(char*)std::malloc(s.size()); if(p)std::memcpy(p,s.data(),s.size()); return p; }
    catch(const std::exception& e){ return dupstr(std::string("ERR:")+e.what()); }
}



static bool known_top(const char* k) {
    return std::strcmp(k,"canvas")==0 || std::strcmp(k,"layers")==0 ||
           std::strcmp(k,"strokes")==0 || std::strcmp(k,"count")==0;
}
static bool known_layer(const char* k) { return std::strcmp(k,"name")==0 || std::strcmp(k,"blend")==0; }
static bool known_stroke(const char* k) {
    return std::strcmp(k,"layer")==0 || std::strcmp(k,"preset")==0 || std::strcmp(k,"size")==0 ||
           std::strcmp(k,"opacity")==0 || std::strcmp(k,"hardness")==0 || std::strcmp(k,"color")==0 ||
           std::strcmp(k,"points")==0 || std::strcmp(k,"tag")==0;
}
static std::string extra_json_for_plan(const Document& d) {
    Document ex; ex.SetObject();
    auto& a = ex.GetAllocator();
    Value top(kObjectType);
    for (auto it = d.MemberBegin(); it != d.MemberEnd(); ++it) {
        if (known_top(it->name.GetString())) continue;
        Value v; v.CopyFrom(it->value, a); top.AddMember(Value(it->name, a), v, a);
    }
    if (!top.ObjectEmpty()) ex.AddMember("top", top, a);
    Value le(kArrayType);
    for (rapidjson::SizeType i = 0; i < d["layers"].Size(); ++i) {
        Value e(kObjectType), x(kObjectType);
        for (auto it = d["layers"][i].MemberBegin(); it != d["layers"][i].MemberEnd(); ++it) {
            if (known_layer(it->name.GetString())) continue;
            Value v; v.CopyFrom(it->value, a); x.AddMember(Value(it->name, a), v, a);
        }
        if (!x.ObjectEmpty()) { e.AddMember("i", i, a); e.AddMember("extra", x, a); le.PushBack(e, a); }
    }
    if (!le.Empty()) ex.AddMember("layers", le, a);
    Value se(kArrayType);
    for (rapidjson::SizeType i = 0; i < d["strokes"].Size(); ++i) {
        Value e(kObjectType), x(kObjectType);
        for (auto it = d["strokes"][i].MemberBegin(); it != d["strokes"][i].MemberEnd(); ++it) {
            if (known_stroke(it->name.GetString())) continue;
            if (std::strcmp(it->name.GetString(), "region_id") == 0 && it->value.IsNumber() && it->value.GetDouble() == 0.0) continue;
            Value v; v.CopyFrom(it->value, a); x.AddMember(Value(it->name, a), v, a);
        }
        if (!x.ObjectEmpty()) { e.AddMember("i", i, a); e.AddMember("extra", x, a); se.PushBack(e, a); }
    }
    if (!se.Empty()) ex.AddMember("strokes", se, a);
    if (ex.ObjectEmpty()) return "";
    StringBuffer sb; Writer<StringBuffer> w(sb); ex.Accept(w);
    return std::string(sb.GetString(), sb.GetSize());
}
static std::string merge_extra_json(const std::string& base, const std::string& extra) {
    Document d; d.Parse(base.c_str(), base.size());
    Document e; e.Parse(extra.c_str(), extra.size());
    if (d.HasParseError() || e.HasParseError() || !d.IsObject() || !e.IsObject()) return base;
    auto& a = d.GetAllocator();
    if (e.HasMember("top") && e["top"].IsObject()) {
        for (auto it = e["top"].MemberBegin(); it != e["top"].MemberEnd(); ++it) {
            if (d.HasMember(it->name)) d.RemoveMember(it->name);
            Value v; v.CopyFrom(it->value, a); d.AddMember(Value(it->name, a), v, a);
        }
    }
    if (e.HasMember("layers") && e["layers"].IsArray() && d["layers"].IsArray()) {
        for (auto& item : e["layers"].GetArray()) {
            int i = item["i"].GetInt();
            if (i < 0 || i >= (int)d["layers"].Size() || !item["extra"].IsObject()) continue;
            Value& l = d["layers"][i];
            for (auto it = item["extra"].MemberBegin(); it != item["extra"].MemberEnd(); ++it) {
                if (l.HasMember(it->name)) l.RemoveMember(it->name);
                Value v; v.CopyFrom(it->value, a); l.AddMember(Value(it->name, a), v, a);
            }
        }
    }
    if (e.HasMember("strokes") && e["strokes"].IsArray() && d["strokes"].IsArray()) {
        for (auto& item : e["strokes"].GetArray()) {
            int i = item["i"].GetInt();
            if (i < 0 || i >= (int)d["strokes"].Size() || !item["extra"].IsObject()) continue;
            Value& l = d["strokes"][i];
            for (auto it = item["extra"].MemberBegin(); it != item["extra"].MemberEnd(); ++it) {
                if (l.HasMember(it->name)) l.RemoveMember(it->name);
                Value v; v.CopyFrom(it->value, a); l.AddMember(Value(it->name, a), v, a);
            }
        }
    }
    StringBuffer sb; Writer<StringBuffer> w(sb); d.Accept(w);
    return std::string(sb.GetString(), sb.GetSize());
}

static std::string plan_json_to_bin(const std::string& input) {
    Document d; d.Parse(input.c_str(), input.size());
    if (d.HasParseError() || !d.IsObject()) throw std::runtime_error("invalid plan json");
    int w=d["canvas"][0].GetInt(), h=d["canvas"][1].GetInt();
    const Value& ls=d["layers"]; const Value& ss=d["strokes"];
    std::map<std::string,int> idx;
    for (rapidjson::SizeType i=0;i<ls.Size();++i) idx[get_str(ls[i],"name","layer")]=(int)i;
    std::string extra = extra_json_for_plan(d);
    std::vector<unsigned char> v; v.reserve(2*1024*1024);
    v.insert(v.end(), {'P','S','V','B'}); wr32(v, extra.empty()?1:2);
    wr32(v,(uint32_t)w); wr32(v,(uint32_t)h); wr32(v,(uint32_t)ls.Size()); wr32(v,(uint32_t)ss.Size());
    for(const auto& l : ls.GetArray()){ wrstr(v,get_str(l,"blend","normal")); wrstr(v,get_str(l,"name","layer")); }
    for(const auto& s : ss.GetArray()){
        wr16(v,(uint16_t)idx[get_str(s,"layer","layer")]); wrstr(v,get_str(s,"preset","round_soft"));
        wrf(v,(float)get_num(s,"size",8.0)); wrf(v,(float)get_num(s,"opacity",1.0)); wrf(v,(float)get_num(s,"hardness",0.7));
        auto ci=s.FindMember("color"); double r=217,g=180,b=90;
        if(ci!=s.MemberEnd()&&ci->value.IsArray()&&ci->value.Size()>=3){r=ci->value[0].GetDouble();g=ci->value[1].GetDouble();b=ci->value[2].GetDouble();}
        wrf(v,(float)r); wrf(v,(float)g); wrf(v,(float)b);
        const Value& pts=s["points"]; wr16(v,(uint16_t)pts.Size());
        for(const auto& p : pts.GetArray()){ wrf(v,(float)p[0].GetDouble()); wrf(v,(float)p[1].GetDouble()); wrf(v,(float)(p.Size()>2?p[2].GetDouble():1.0)); }
        wrstr(v, s.HasMember("tag")?s["tag"].GetString():"");
    }
    if (!extra.empty()) { v.insert(v.end(), extra.begin(), extra.end()); wr32(v, (uint32_t)extra.size()); }
    return std::string((const char*)v.data(), v.size());
}

static std::string bin_to_plan_json(const unsigned char* data, int len) {
    BR b(data,len);
    if(b.u32()!=0x42565350) throw std::runtime_error("bad binary magic");
    { uint32_t v=b.u32(); if(v!=1&&v!=2) throw std::runtime_error("bad binary version"); }
    int w=b.i32(),h=b.i32(),lc=b.i32(),sc=b.i32();
    std::vector<std::string> names(lc),blends(lc);
    for(int i=0;i<lc;++i){ blends[i]=b.str16(); names[i]=b.str16(); }
    std::string out="{\"canvas\":["; out+=std::to_string(w); out+=","; out+=std::to_string(h); out+="],\"layers\":[";
    for(int i=0;i<lc;++i){ if(i)out+=","; out+="{\"name\":\""; jesc_to(out,names[i]); out+="\",\"blend\":\""; jesc_to(out,blends[i]); out+="\"}"; }
    out+="],\"strokes\":[";
    for(int k=0;k<sc;++k){
        if(k)out+=",";
        int li=b.u16(); std::string preset=b.str16();
        float size=b.f32(),op=b.f32(),hard=b.f32(),r=b.f32(),g=b.f32(),bl=b.f32();
        int pc=b.u16();
        out+="{\"layer\":\""; jesc_to(out,names[li]); out+="\",\"preset\":\""; jesc_to(out,preset);
        out+="\",\"size\":"+fmt(size)+",\"opacity\":"+fmt(op)+",\"hardness\":"+fmt(hard)+",\"color\":["+fmt(r)+","+fmt(g)+","+fmt(bl)+"],\"points\":[";
        for(int j=0;j<pc;++j){ if(j)out+=","; float x=b.f32(),y=b.f32(),pr=b.f32(); out+="["+fmt(x)+","+fmt(y)+","+fmt(pr)+"]"; }
        out+="]";
        std::string tag=b.str16(); if(!tag.empty()){ out+=",\"tag\":\""; jesc_to(out,tag); out+="\""; }
        out+="}";
    }
    out+="],\"count\":"+std::to_string(sc)+"}";
    if (b.i >= 4 && b.n >= b.i + 4) {
        uint32_t n; std::memcpy(&n, data + len - 4, 4);
        if (n <= (uint32_t)(len - 4) && len - 4 - (int)n >= 0) {
            std::string extra((const char*)data + len - 4 - n, n);
            return merge_extra_json(out, extra);
        }
    }
    return out;
}
EXPORT char* psvg_plan_json_to_bin(const char* in,int n,int* out_len){
    try{std::string s=plan_json_to_bin(std::string(in,n));*out_len=(int)s.size();char*p=(char*)std::malloc(s.size());if(p)std::memcpy(p,s.data(),s.size());return p;}
    catch(const std::exception&e){*out_len=(int)(std::string("ERR:")+e.what()).size();return dupstr(std::string("ERR:")+e.what());}
}
EXPORT char* psvg_bin_to_plan_json(const char* in,int n,int* out_len){
    try{std::string s=bin_to_plan_json((const unsigned char*)in,n);*out_len=(int)s.size();char*p=(char*)std::malloc(s.size());if(p)std::memcpy(p,s.data(),s.size());return p;}
    catch(const std::exception&e){*out_len=(int)(std::string("ERR:")+e.what()).size();return dupstr(std::string("ERR:")+e.what());}
}


static std::string bin_to_svg_stamps(const unsigned char* data, int len) {
    BR b(data,len);
    if(b.u32()!=0x42565350) throw std::runtime_error("bad PSVB");
    { uint32_t v=b.u32(); if(v!=1&&v!=2) throw std::runtime_error("bad PSVB version"); }
    int w=b.i32(),h=b.i32(),lc=b.i32(),sc=b.i32();
    std::vector<std::string> names(lc),blends(lc);
    for(int i=0;i<lc;++i){ blends[i]=b.str16(); names[i]=b.str16(); }
    std::vector<BStroke> strokes; strokes.reserve(sc);
    for(int k=0;k<sc;++k){
        BStroke s; s.layer=b.u16(); s.preset=b.str16();
        s.size=b.f32(); s.opacity=b.f32(); s.hardness=b.f32();
        s.r=b.f32(); s.g=b.f32(); s.b=b.f32();
        int pc=b.u16(); s.pts.reserve(pc*3);
        for(int j=0;j<pc;++j){ s.pts.push_back(b.f32()); s.pts.push_back(b.f32()); s.pts.push_back(b.f32()); }
        s.tag=b.str16(); strokes.push_back(std::move(s));
    }
    std::string out; out.reserve(64*1024*1024);
    out += "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"" + std::to_string(w) + "\" height=\"" + std::to_string(h) + "\" viewBox=\"0 0 " + std::to_string(w) + " " + std::to_string(h) + "\" data-bridge=\"plan_svg_stamps\" shape-rendering=\"geometricPrecision\">\n";
    for(int li=0;li<lc;++li){
        out += "<g data-name=\"" + names[li] + "\" data-blend=\"" + blends[li] + "\">\n";
        for(const auto& s:strokes) if(s.layer==li){
            std::string ca;
            for(size_t j=0;j+2<s.pts.size();j+=3){ if(!ca.empty())ca += ";"; ca += fmt(s.pts[j])+","+fmt(s.pts[j+1])+","+fmt(s.pts[j+2]); }
            out += "<g data-stroke=\"1\" data-layer=\""; esc_to(out,names[li]); out += "\" data-preset=\""; esc_to(out,s.preset);
            out += "\" data-size=\""+fmt(s.size)+"\" data-opacity=\""+fmt(s.opacity)+"\" data-hardness=\""+fmt(s.hardness)+"\" data-color=\""+fmt(s.r)+","+fmt(s.g)+","+fmt(s.b)+"\" data-center=\""; esc_to(out,ca); out += "\" data-center-hash=\""+hash_text(ca)+"\"";
            if(!s.tag.empty()){ out += " data-tag=\""; esc_to(out,s.tag); out += "\""; }
            out += ">\n";
            const float baseR=s.size*0.5f;
            float total=0;
            for(size_t i=1;i+2<s.pts.size();i+=3) total += std::sqrt((s.pts[i]-s.pts[i-3])*(s.pts[i]-s.pts[i-3])+(s.pts[i+1]-s.pts[i-2])*(s.pts[i+1]-s.pts[i-2]));
            float arc=0;
            for(size_t i=3;i+2<s.pts.size();i+=3){
                float x0=s.pts[i-3], y0=s.pts[i-2], p0=s.pts[i-1];
                float x1=s.pts[i], y1=s.pts[i+1], p1=s.pts[i+2];
                float dx=x1-x0, dy=y1-y0, ln=std::sqrt(dx*dx+dy*dy);
                float step=std::max(0.6f, baseR*0.30f);
                int steps=(int)std::floor(ln/step); if(steps<1)steps=1;
                for(int k=0;k<=steps;++k){
                    float t=(float)k/(float)steps;
                    float x=x0+dx*t,y=y0+dy*t,press=p0+(p1-p0)*t;
                    float pr=0.30f+0.70f*press, pa=0.15f+0.85f*press;
                    float rr=baseR*pr, op=std::max(0.0f,std::min(1.0f,s.opacity*pa));
                    if(rr<=0)continue;
                    char hex[8]; std::snprintf(hex,sizeof(hex),"#%02x%02x%02x",(int)std::lround(s.r),(int)std::lround(s.g),(int)std::lround(s.b));
                    out += "<circle cx=\""+fmt(x)+"\" cy=\""+fmt(y)+"\" r=\""+fmt(rr)+"\" fill=\""+hex+"\" fill-opacity=\""+fmt(op)+"\"/>\n";
                }
                arc+=ln;
            }
            out += "</g>\n";
        }
        out += "</g>\n";
    }
    out += "</svg>\n";
    return out;
}
EXPORT char* psvg_bin_to_svg_stamps(const char* data,int n,int* out_len){
    try{std::string s=bin_to_svg_stamps((const unsigned char*)data,n);*out_len=(int)s.size();char*p=(char*)std::malloc(s.size());if(p)std::memcpy(p,s.data(),s.size());return p;}
    catch(const std::exception&e){*out_len=(int)(std::string("ERR:")+e.what()).size();return dupstr(std::string("ERR:")+e.what());}
}

EXPORT unsigned char* psvg_parse_d_bin(const char* in, int n, int* out_len) {
    try {
        std::string d(in, (size_t)n);
        std::vector<std::vector<double>> subs;
        std::vector<std::string> parts = split_d_subpaths(d);
        for (auto& part : parts) {
            std::vector<double> xs, ys;
            parse_d(part, xs, ys);
            if (xs.size() < 2) continue;
            std::vector<double> v;
            v.reserve(xs.size() * 2);
            for (size_t i = 0; i < xs.size(); ++i) { v.push_back(xs[i]); v.push_back(ys[i]); }
            subs.push_back(std::move(v));
        }
        size_t total = 4;
        for (auto& v : subs) total += 4 + v.size() * 8;
        unsigned char* buf = (unsigned char*)std::malloc(total);
        if (!buf) return nullptr;
        unsigned char* p = buf;
        uint32_t ns = (uint32_t)subs.size();
        std::memcpy(p, &ns, 4); p += 4;
        for (auto& v : subs) {
            uint32_t np = (uint32_t)(v.size() / 2);
            std::memcpy(p, &np, 4); p += 4;
            if (!v.empty()) { std::memcpy(p, v.data(), v.size() * 8); p += v.size() * 8; }
        }
        if (out_len) *out_len = (int)total;
        return buf;
    } catch (...) { return nullptr; }
}


EXPORT void psvg_free(char* p){ std::free(p); }
