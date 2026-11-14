/* Apps and packages of a new system (2026-11-08): a page with every app of the list, and a
   search in the repositories of the chosen distribution, so an extra package is named as the
   distribution names it and is known to exist for arm64. distro.py package-search asks the
   distribution's own package site; the chosen names go into the plan as --package. */
static const char*const pkg_families[]={"debian","ubuntu","fedora","arch","manjaro"};
static const char*const pkg_family_labels[]={"Debian","Ubuntu","Fedora","Arch","Manjaro"};
#define PKG_FAMILIES (sizeof(pkg_families)/sizeof(pkg_families[0]))

/* The release name of a catalog image's version (2026-11-14 review: every Ubuntu but 24.04 was
   taken for 26.04 and every Debian for 13; a newer image in the remote catalog was searched in the
   wrong release). A version not in the table names none: the helper then searches its default. */
static const char*pkg_release_of(const char*family,const char*version){
    static const struct{const char*family,*version,*release;}known[]={
        {"debian","12","bookworm"},{"debian","13","trixie"},{"debian","14","forky"},
        {"mobian","12","bookworm"},{"mobian","13","trixie"},{"mobian","14","forky"},
        {"ubuntu","22.04","jammy"},{"ubuntu","24.04","noble"},{"ubuntu","24.10","oracular"},{"ubuntu","25.04","plucky"},
        {"ubuntu","25.10","questing"},{"ubuntu","26.04","resolute"}};
    for(size_t i=0;i<sizeof(known)/sizeof(known[0]);i++){
        size_t n=strlen(known[i].version);
        if(!strcmp(family,known[i].family)&&!strncmp(version,known[i].version,n)&&(version[n]==0||version[n]==' '||version[n]=='.'))return known[i].release;
    }
    return "";
}
/* The family and release to search: those of the catalog image, or the chosen chip for a file. */
static bool pkg_target(const App*a,char*family,size_t fsize,char*release,size_t rsize,char*label,size_t lsize){
    release[0]=0;
    if(a->systems_remote_id[0])for(unsigned int i=0;i<a->catalog_count;i++){
        const CatalogImage*c=&a->catalog[i];if(strcmp(c->id,a->systems_remote_id))continue;
        char text_[112];snprintf(text_,sizeof(text_),"%s %s",c->name,c->version);copy(label,lsize,text_);
        if(!strcmp(c->family,"debian")||!strcmp(c->family,"mobian")||!strcmp(c->family,"ubuntu")){copy(family,fsize,c->family);copy(release,rsize,pkg_release_of(c->family,c->version));}
        else if(!strcmp(c->family,"fedora")){char number[3]={c->version[0],c->version[0]?c->version[1]:0,0};copy(family,fsize,"fedora");copy(release,rsize,number);}
        else copy(family,fsize,c->family);
        return true;
    }
    unsigned int n=a->pkg_family<PKG_FAMILIES?a->pkg_family:0u;
    copy(family,fsize,pkg_families[n]);copy(label,lsize,pkg_family_labels[n]);return false;
}
static bool pkg_chosen(const App*a,const char*name){
    for(unsigned int i=0;i<a->systems_package_count;i++)if(!strcmp(a->systems_packages[i],name))return true;
    return false;
}
static void pkg_toggle(App*a,const char*name){
    for(unsigned int i=0;i<a->systems_package_count;i++)if(!strcmp(a->systems_packages[i],name)){
        memmove(a->systems_packages[i],a->systems_packages[i+1],(size_t)(a->systems_package_count-i-1)*sizeof(a->systems_packages[0]));
        a->systems_package_count--;a->dirty=true;return;
    }
    if(a->systems_package_count<PACKAGES_MAX){copy(a->systems_packages[a->systems_package_count++],sizeof(a->systems_packages[0]),name);a->dirty=true;}
}
static unsigned int systems_app_count(const App*a){unsigned int n=0;for(unsigned int i=0;i<SYSTEMS_APPS;i++)n+=(a->systems_apps>>i)&1u;return n;}

/* The apps card of "Network and apps": what is chosen, and the two ways to choose more. */
static void render_apps_card(App*a,int y){
    rounded(a,(Rect){28,y,664,346},18,PANEL);text(a,52,y+20,0,ORANGE,"APPS");
    char line[320]={0};unsigned int shown=0,count=systems_app_count(a);
    for(unsigned int i=0;i<SYSTEMS_APPS&&shown<4;i++)if((a->systems_apps>>i)&1u){if(shown)append(line,sizeof(line),", ");append(line,sizeof(line),tr(systems_app_labels[i]));shown++;}
    if(count>shown){char more[48];snprintf(more,sizeof(more),T(" and %u more"),count-shown);append(line,sizeof(line),more);}
    {const char*none=tr("No apps chosen");/* Before raw mode, which turns translation off. */
    raw_text(true);wrapped(a,52,y+60,610,0,count?TEXT:MUTED,count?line:none,2);raw_text(false);}
    line[0]=0;
    for(unsigned int i=0;i<a->systems_package_count&&i<5;i++){if(i)append(line,sizeof(line),", ");append(line,sizeof(line),a->systems_packages[i]);}
    if(a->systems_package_count>5){char more[48];snprintf(more,sizeof(more),T(" and %u more"),a->systems_package_count-5);append(line,sizeof(line),more);}
    char packages[360];snprintf(packages,sizeof(packages),T("Packages: %s"),a->systems_package_count?line:tr("none"));
    raw_text(true);wrapped(a,52,y+140,610,0,MUTED,packages,2);raw_text(false);
    button(a,(Rect){52,y+246,296,76},"Choose apps",A_APPS_OPEN,ui_free(a),false,0);
    button(a,(Rect){372,y+246,296,76},"Search packages",A_PKG_OPEN,ui_free(a),false,0);
}

#include "app_icons_data.h"
/* The icons an app shows are those of the package the new system gets: the main shell decides it
   (KDE apps for Plasma, Xfce's for Xfce...) and the distribution which candidate it has (2026-11-09). */
static unsigned int app_icon_style(const App*a){
    const char*d=a->systems_desktop<SYSTEMS_DESKTOPS?systems_desktops[a->systems_desktop]:"";
    static const char*const shells[]={"plasma","xfce","lomiri","sxmo"};
    for(unsigned int n=0;n<4;n++)if(!strcmp(d,shells[n]))return n+1;
    return 0;
}
static unsigned int app_icon_family(const App*a){
    const char*f=systems_image_family(a);
    if(!f)return 0;
    if(!strcmp(f,"ubuntu"))return 1;
    if(!strcmp(f,"fedora"))return 2;
    if(!strcmp(f,"arch")||!strcmp(f,"manjaro"))return 3;
    return 0;
}
static int app_icon_index(const App*a,unsigned int app){
    return app<SYSTEMS_APPS?app_icon_of[app_icon_style(a)][app_icon_family(a)][app]:-1;
}
/* Services and on-screen keyboards have no app icon of their own: a plain symbol stands in. */
static int app_symbol(unsigned int app){
    const char*id=systems_app_ids[app];
    if(!strcmp(id,"keyboard"))return ICON_KEYBOARD;

    if(!strcmp(id,"files"))return A_FILES;
    if(!strcmp(id,"settings"))return ICON_TUNE;
    if(!strcmp(id,"browser"))return ICON_BROWSER;
    if(!strcmp(id,"clock"))return ICON_CLOCK;
    return ICON_LINUX;
}
static void render_systems_apps(App*a){
    heading(a,"Apps","For each, the package of the new system is installed.",true);
    Bounds b=canvas_bounds(a);
    for(unsigned int n=0;n<SYSTEMS_APPS;n++){
        Rect r={28+(int)(n%3)*226,232+(int)(n/3)*106,212,100};bool on=(a->systems_apps>>n)&1u;
        surface(a,r,20,on?ORANGE_DARK:PANEL,A_SYS_APP+(int)n);
        int picture=app_icon_index(a,n);
        if(picture>=0)canvas_rgba(a->pixels,b,nx(r.x+r.w/2)-APP_ICON_PX/2,nx(r.y+8),app_icon_rgba[picture],APP_ICON_PX,APP_ICON_PX);
        else icon(a,app_symbol(n),r.x+r.w/2-22,r.y+10,44,on?ORANGE:MUTED);
        /* The name sits centred under the icon; one too long for the tile is shortened. */
        const char*label=tr(systems_app_labels[n]);uint32_t ink=on?ORANGE:TEXT;
        raw_text(true);
        if(text_width(label,0)<=r.w-20)centered(a,(Rect){r.x+10,r.y+60,r.w-20,34},0,ink,label);
        else ellipsis(a,r.x+10,r.y+62,r.w-20,0,ink,label);
        raw_text(false);
        if(on){
            int cx=r.x+r.w-24,cy=r.y+22;uint32_t ink=ui_theme->hero_text;
            rounded(a,(Rect){cx-15,cy-15,30,30},15,ORANGE);
            vector_line(a,cx-8,cy+1,cx-3,cy+6,3,ink);vector_line(a,cx-3,cy+6,cx+8,cy-6,3,ink);
        }
        register_button(a,r,A_SYS_APP+(int)n,ui_free(a),0);
    }
    char count[64];snprintf(count,sizeof(count),T("%u apps chosen"),systems_app_count(a));centered(a,(Rect){28,1290,664,36},0,MUTED,count);
    button(a,(Rect){28,1336,664,100},"Done",A_APPS_DONE,true,true,0);
}

static void render_package_search(App*a){
    char family[24],release[24],label[112];bool known=pkg_target(a,family,sizeof(family),release,sizeof(release),label,sizeof(label));
    char note[160];snprintf(note,sizeof(note),T("In the repositories of %s"),label);
    heading(a,"Search packages",note,true);
    bool busy=a->job.pid&&a->job.kind==J_PKG_SEARCH;
    /* The query and every result scroll under the finger; Done stays below (2026-11-08). */
    a->page_scroll=(int64_t)scroll_clamp((double)a->page_scroll,(double)page_max_scroll(a));int y=232-(int)a->page_scroll;
    a->clip_active=true;a->clip=page_viewport(a);
    if(!known){
        /* A file can hold any distribution: its family is chosen here. */
        int x=28;
        for(unsigned int n=0;n<PKG_FAMILIES;n++){
            int w=text_width(pkg_family_labels[n],0)+40;Rect r={x,y,w,52};
            rounded(a,r,26,a->pkg_family==n?ORANGE:PANEL);centered(a,r,0,a->pkg_family==n?ui_theme->hero_text:TEXT,pkg_family_labels[n]);
            register_button(a,r,A_PKG_FAMILY+(int)n,!busy,0);x+=w+10;
        }
        y+=72;
    }
    /* The query: a tap opens the keyboard; the search runs when it is entered. */
    Rect field={28,y,664,92};surface(a,field,22,PANEL,A_PKG_QUERY);
    icon(a,ICON_BROWSER,48,y+24,44,ORANGE);
    {const char*hint=tr("Name or part of it");
    raw_text(true);ellipsis(a,112,y+28,540,1,a->pkg_query[0]?TEXT:MUTED,a->pkg_query[0]?a->pkg_query:hint);raw_text(false);}
    register_button(a,field,A_PKG_QUERY,!busy,0);y+=108;
    if(busy){wrapped(a,40,y+10,640,0,MUTED,"Searching the repositories...",2);y+=70;}
    else if(a->pkg_error[0]){const char*why=tr(a->pkg_error);raw_text(true);y+=wrapped(a,40,y+10,640,0,RED,why,3)+20;raw_text(false);}
    else if(a->pkg_known&&!a->pkg_count){wrapped(a,40,y+10,640,0,MUTED,"No package with this name.",2);y+=70;}
    for(unsigned int i=0;i<a->pkg_count&&!busy;i++){
        const PkgResult*p=&a->pkg_results[i];bool chosen=pkg_chosen(a,p->name),usable=p->available!=0;
        Rect r={28,y,664,100};surface(a,r,22,chosen?ui_theme->tile_fill[2]:PANEL,A_PKG_RESULT+(int)i);
        char title[120];snprintf(title,sizeof(title),"%s  %s",p->name,p->version);
        raw_text(true);ellipsis(a,52,y+12,470,1,usable?TEXT:MUTED,title);ellipsis(a,52,y+58,470,0,MUTED,p->summary);raw_text(false);
        const char*state=chosen?"Added":(p->available>0?"arm64":(p->available==0?"Not for arm64":"Available?"));
        uint32_t ink=chosen?GREEN:(p->available>0?GREEN:(p->available==0?RED:MUTED));
        Rect tag={540,y+30,136,40};rounded(a,tag,20,PANEL2);centered(a,tag,0,ink,state);
        register_button(a,r,A_PKG_RESULT+(int)i,usable&&ui_free(a),0);y+=108;
    }
    a->page_content=y+a->page_scroll-230+16;a->clip_active=false;scroll_indicator(a,(Rect){698,230,5,1086},a->page_scroll,a->page_content,ORANGE);
    char done[80];snprintf(done,sizeof(done),T("Done: %u packages"),a->systems_package_count);
    button(a,(Rect){28,1330,664,108},done,A_PKG_DONE,true,true,0);
}

static bool start_package_search(App*a){
    if(a->job.pid||a->preview||!a->distro_helper[0]||!a->pkg_query[0])return false;
    char family[24],release[24],label[112];pkg_target(a,family,sizeof(family),release,sizeof(release),label,sizeof(label));
    copy(a->pkg_search_family,sizeof(a->pkg_search_family),family);copy(a->pkg_search_release,sizeof(a->pkg_search_release),release);
    char*args[16];int n=0;args[n++]=a->python;args[n++]=a->distro_helper;
    if(a->device)args[n++]="--device";else{args[n++]="--fixture";args[n++]=a->fixture;}
    args[n++]="package-search";args[n++]="--family";args[n++]=a->pkg_search_family;
    if(release[0]){args[n++]="--release";args[n++]=a->pkg_search_release;}
    args[n++]="--query";args[n++]=a->pkg_query;args[n]=NULL;
    a->pkg_error[0]=0;a->pkg_count=0;a->pkg_known=false;
    return launch_job(a,J_PKG_SEARCH,args);
}
static void complete_package_search(App*a,const Json*j,bool ok){
    a->pkg_known=true;a->pkg_count=0;a->page_scroll=0;
    if(!ok||!j){
        if(j){int e=json_get(j,0,"error");if(e>=0)jstring(j,e,"message",a->pkg_error,sizeof(a->pkg_error));}
        if(!a->pkg_error[0])copy(a->pkg_error,sizeof(a->pkg_error),"The package site did not answer. Check Wi-Fi and try again.");
        a->dirty=true;return;
    }
    int list=json_get(j,0,"results");
    if(list>=0)for(int i=list+1;i<json_next(j,list)&&a->pkg_count<PKG_RESULTS;i=json_next(j,i)){
        PkgResult*p=&a->pkg_results[a->pkg_count];memset(p,0,sizeof(*p));
        jstring(j,i,"name",p->name,sizeof(p->name));jstring(j,i,"version",p->version,sizeof(p->version));jstring(j,i,"summary",p->summary,sizeof(p->summary));
        int av=json_get(j,i,"available");
        p->available=av<0||(j->tokens[av].type==J_PRIMITIVE&&j->text[j->tokens[av].start]=='n')?-1:(json_bool(j,av,0)?1:0);
        if(p->name[0])a->pkg_count++;
    }
    a->dirty=true;
}
/* The query prompt: one field and the keyboard. */
static void render_package_prompt(App*a){
    heading(a,"Search packages","Letters, digits, + . _ or -; at least two.",true);
    account_field(a,236,"PACKAGE NAME",a->fs_prompt,false,true,A_PKG_QUERY);
    render_keyboard(a);
    button(a,(Rect){28,1370,256,84},"Cancel",A_BACK,ui_free(a),false,0);
    button(a,(Rect){304,1370,388,84},"Search",A_FS_PROMPT_NEXT,ui_free(a)&&strlen(a->fs_prompt)>=2,true,0);
}
static bool pkg_query_ok(const char*q){
    size_t n=strlen(q);if(n<2||n>40)return false;
    for(size_t i=0;i<n;i++){unsigned char c=(unsigned char)q[i];if(!isalnum(c)&&c!='+'&&c!='.'&&c!='_'&&c!='-')return false;}
    return isalnum((unsigned char)q[0]);
}
static bool packages_action(App*a,int id){
    if(a->screen==SYSTEMS_EXTRAS){
        if(id==A_APPS_OPEN){screen(a,SYSTEMS_APP_LIST);return true;}
        if(id==A_PKG_OPEN){a->page_scroll=0;screen(a,SYSTEMS_SEARCH);return true;}
    }
    if(a->screen==SYSTEMS_APP_LIST){
        if(id>=A_SYS_APP&&id<A_SYS_APP+(int)SYSTEMS_APPS){a->systems_apps^=1u<<(id-A_SYS_APP);a->dirty=true;return true;}
        if(id==A_APPS_DONE||id==A_BACK){screen(a,SYSTEMS_EXTRAS);return true;}
    }
    if(a->screen==SYSTEMS_SEARCH){
        if(id==A_PKG_DONE||id==A_BACK){screen(a,SYSTEMS_EXTRAS);return true;}
        if(id>=A_PKG_FAMILY&&id<A_PKG_FAMILY+(int)PKG_FAMILIES){a->pkg_family=(unsigned int)(id-A_PKG_FAMILY);a->pkg_count=0;a->pkg_known=false;a->pkg_error[0]=0;a->page_scroll=0;a->dirty=true;return true;}
        if(id==A_PKG_QUERY){a->fs_prompt_kind=J_PKG_QUERY;copy(a->fs_prompt,sizeof(a->fs_prompt),a->pkg_query);a->prompt_cursor=strlen(a->fs_prompt);a->prompt_scroll=0;screen(a,FILE_PROMPT);return true;}
        if(id>=A_PKG_RESULT&&id<A_PKG_RESULT+(int)PKG_RESULTS){unsigned int n=(unsigned int)(id-A_PKG_RESULT);if(n<a->pkg_count&&a->pkg_results[n].available!=0){char name[64];copy(name,sizeof(name),a->pkg_results[n].name);pkg_toggle(a,name);}return true;}
    }
    if(a->screen==FILE_PROMPT&&a->fs_prompt_kind==J_PKG_QUERY){
        if(id==A_BACK){a->fs_prompt_kind=0;screen(a,SYSTEMS_SEARCH);return true;}
        if(id==A_FS_PROMPT_NEXT||id==A_KB_ENTER){
            if(!pkg_query_ok(a->fs_prompt)){/* the results of the last query go with it (2026-11-14 review) */a->pkg_count=0;a->pkg_known=false;copy(a->pkg_error,sizeof(a->pkg_error),"Use 2 to 40 letters, digits, + . _ or -.");a->fs_prompt_kind=0;screen(a,SYSTEMS_SEARCH);return true;}
            copy(a->pkg_query,sizeof(a->pkg_query),a->fs_prompt);a->fs_prompt_kind=0;screen(a,SYSTEMS_SEARCH);start_package_search(a);return true;
        }
    }
    return false;
}
