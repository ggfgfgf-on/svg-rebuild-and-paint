// Windows plan <-> SVG bridge: RapidJSON (plan JSON), pugixml (SVG).
#include "rapidjson/document.h"
#include "pugixml.hpp"
#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
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
static void parse_d(const std::string& d, std::vector<double>& xs, std::vector<double>& ys) {
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
EXPORT void psvg_free(char* p){ std::free(p); }
