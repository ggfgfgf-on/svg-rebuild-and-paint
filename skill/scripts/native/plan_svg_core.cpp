// Windows plan <-> SVG bridge: RapidJSON (plan JSON), pugixml (SVG).
#include "rapidjson/document.h"
#include "rapidjson/writer.h"
#include "rapidjson/stringbuffer.h"
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include "pugixml.hpp"
#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <sstream>
#include <wchar.h>
#include <fstream>
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
    std::string out;
    out.reserve(16 * 1024 * 1024);
    out += "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n";
    out += "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"" + std::to_string(w) +
           "\" height=\"" + std::to_string(h) + "\" viewBox=\"0 0 " +
           std::to_string(w) + " " + std::to_string(h) +
           "\" data-bridge=\"plan_svg\" shape-rendering=\"geometricPrecision\">\n";
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
            out += "<path d=\""; out += d_from(*pts);
            out += "\" fill=\"none\" stroke=\""; out += color_hex(col);
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
           c=='C'||c=='c'||c=='S'||c=='s'||c=='Q'||c=='q'||c=='T'||c=='t'||c=='Z'||c=='z';
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
        } else break;
    }
}

static std::vector<double> nums(const std::string& s) {
    std::vector<double> v; const char* p=s.c_str();
    while(*p){ while(*p && (std::isspace((unsigned char)*p)||*p==','))++p; if(!*p)break; char*q=nullptr; double x=std::strtod(p,&q); if(q==p)break; v.push_back(x); p=q; }
    return v;
}
static std::vector<double> color_from(const std::string& s) {
    if (s.size()==7 && s[0]=='#') {
        auto h=[](char c)->int{ if(c>='0'&&c<='9')return c-'0'; if(c>='a'&&c<='f')return c-'a'+10; if(c>='A'&&c<='F')return c-'A'+10; return 0; };
        return {double(h(s[1])*16+h(s[2])),double(h(s[3])*16+h(s[4])),double(h(s[5])*16+h(s[6]))};
    }
    auto v=nums(s); if(v.size()<3) return {217,180,90}; return {v[0],v[1],v[2]};
}

static void add_layer(std::vector<std::string>& names, std::vector<std::string>& blends, std::map<std::string,int>& idx, const std::string& n, const std::string& b) {
    if (idx.find(n)!=idx.end()) return;
    idx[n]=(int)names.size(); names.push_back(n); blends.push_back(b.empty()?"normal":b);
}

static std::string plan_from_svg(const std::string& input) {
    pugi::xml_document doc;
    if (!doc.load_buffer(input.c_str(), input.size())) return "ERR: XML parse failed";
    pugi::xml_node root = doc.document_element();
    int W=1200,H=900;
    auto vb = nums(root.attribute("viewBox").as_string());
    if (vb.size()>=4) { W=(int)vb[2]; H=(int)vb[3]; }
    else { if(root.attribute("width"))W=root.attribute("width").as_int(); if(root.attribute("height"))H=root.attribute("height").as_int(); }
    std::vector<std::string> names, blends;
    std::map<std::string,int> idx;
    std::vector<std::string> strokes;
    std::function<void(pugi::xml_node,std::string)> walk = [&](pugi::xml_node node, std::string inherit) {
        for (pugi::xml_node ch=node.first_child(); ch; ch=ch.next_sibling()) {
            std::string tag=ch.name();
            auto at=[&](const char* k){ return std::string(ch.attribute(k).as_string()); };
            if (tag=="g") {
                std::string n=at("data-name"); if(n.empty())n=at("id"); if(n.empty())n=inherit.empty()?"layer":inherit;
                add_layer(names,blends,idx,n,at("data-blend"));
                walk(ch,n);
            } else if (tag=="path") {
                std::string layer=at("data-layer"); if(layer.empty())layer=inherit.empty()?"layer":inherit;
                add_layer(names,blends,idx,layer,"");
                std::vector<double> xs,ys; parse_d(at("d"),xs,ys); if(xs.empty())continue;
                auto ps=nums(at("data-p")); if(ps.size()!=xs.size())ps.assign(xs.size(),1.0);
                std::string ct=at("data-color");
                auto col=ct.empty()?color_from(at("stroke")):color_from(ct);
                std::string sj="{\"layer\":\""; jesc_to(sj,layer);
                sj += "\",\"preset\":\""; jesc_to(sj,at("data-preset").empty()?"round_soft":at("data-preset"));
                sj += "\",\"size\":"; sj += fmt(at("data-size").empty()?std::atof(at("stroke-width").c_str()):std::atof(at("data-size").c_str()));
                sj += ",\"opacity\":"; sj += fmt(at("data-opacity").empty()?1.0:std::atof(at("data-opacity").c_str()));
                sj += ",\"hardness\":"; sj += fmt(at("data-hardness").empty()?0.7:std::atof(at("data-hardness").c_str()));
                sj += ",\"color\":["; sj += fmt(col[0]); sj += ","; sj += fmt(col[1]); sj += ","; sj += fmt(col[2]); sj += "],\"points\":[";
                for(size_t i=0;i<xs.size();++i){ if(i)sj += ","; sj += "["; sj += fmt(xs[i]); sj += ","; sj += fmt(ys[i]); sj += ","; sj += fmt(ps[i]); sj += "]"; }
                sj += "]";
                if(!at("data-tag").empty()){ sj += ",\"tag\":\""; jesc_to(sj,at("data-tag")); sj += "\""; }
                sj += "}";
                strokes.push_back(sj);
            } else walk(ch,inherit);
        }
    };
    walk(root,"");
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
EXPORT char* psvg_plan_to_svg(const char* in,int n){ try{return dupstr(svg_from_plan(std::string(in,n)));}catch(const std::exception&e){return dupstr(std::string("ERR:")+e.what());} }
EXPORT char* psvg_svg_to_plan(const char* in,int n){ try{return dupstr(plan_from_svg(std::string(in,n)));}catch(const std::exception&e){return dupstr(std::string("ERR:")+e.what());} }
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
        std::string input = read_all(svg_path);
        std::string bin; try { bin = scan_from_svg(input); } catch (...) { bin = bin_from_svg(input); }
        std::string out = bin_to_plan_json((const unsigned char*)bin.data(), (int)bin.size());
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
    try { std::string s; try { s=scan_from_svg(std::string(svg,n)); } catch (...) { s=bin_from_svg(std::string(svg,n)); } *out_len=(int)s.size(); char* p=(char*)std::malloc(s.size()); if(p)std::memcpy(p,s.data(),s.size()); return p; }
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
            out += "\" data-size=\""+fmt(s.size)+"\" data-opacity=\""+fmt(s.opacity)+"\" data-hardness=\""+fmt(s.hardness)+"\" data-color=\""+fmt(s.r)+","+fmt(s.g)+","+fmt(s.b)+"\" data-center=\""; esc_to(out,ca); out += "\"";
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

EXPORT void psvg_free(char* p){ std::free(p); }
