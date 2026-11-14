/* SPDX-License-Identifier: MIT
 * Language and time zone of a new system (2026-11-09). By default the recovery's own language
 * and clock offset; a city brings summer time where its region has it. distro.py checks the
 * choice and a22x-firstboot.sh makes the locale and links the zone of the new system's tz data.
 */
#ifndef A22X_LOCALE_UI_H
#define A22X_LOCALE_UI_H
#include "zones_data.h"

/* The names shown for the regions of zones_data.h, as literals so that they are translated. They
   follow tz_regions one for one: the count is checked here and each name against its region in the
   self-tests (2026-11-14 review: the two lists could drift apart unnoticed). */
static const char*const tz_region_labels[]={"Europe","America","Asia","Africa","Australia","Pacific","Atlantic","Indian Ocean","Antarctica","Arctic"};
_Static_assert(sizeof(tz_region_labels)/sizeof(*tz_region_labels)==TZ_REGIONS,"one label for each region of zones_data.h");
static const char*const sys_locales[]={"en_US","uk_UA","de_DE","fr_FR","pl_PL","es_ES","it_IT","pt_BR","nl_NL","cs_CZ","ro_RO","tr_TR","ru_RU"};
static const char*const sys_locale_names[]={"English","Українська","Deutsch","Français","Polski","Español","Italiano","Português","Nederlands","Čeština","Română","Türkçe","Русский"};
#define SYS_LOCALES 13u

/* The system language follows the recovery's until one is picked. */
static unsigned int locale_index(const App*a){
    if(a->systems_locale>=0&&(unsigned int)a->systems_locale<SYS_LOCALES)return (unsigned int)a->systems_locale;
    const char*code=language_codes[ui_language<LANGUAGE_COUNT?ui_language:0];
    for(unsigned int i=0;i<SYS_LOCALES;i++)if(!strncmp(sys_locales[i],code,2))return i;
    return 0;
}
static const TzZone*tz_chosen(const App*a){return a->systems_tz>=0&&(unsigned int)a->systems_tz<TZ_ZONES?&tz_zones[a->systems_tz]:NULL;}
/* +2 or +5:30: what utc_label writes after UTC (one formatter, 2026-11-14 review). */
static void tz_offset_text(int minutes,char*out,size_t size){char label[24];utc_label(minutes,label,sizeof(label));snprintf(out,size,"%s",label+3);}
static void tz_zone_offsets(const TzZone*z,char*out,size_t size){
    char a1[12],a2[12];tz_offset_text(z->january,a1,sizeof(a1));tz_offset_text(z->july,a2,sizeof(a2));
    if(z->january==z->july)snprintf(out,size,"UTC%s",a1);else snprintf(out,size,"UTC%s / %s",a1,a2);
}
/* The recovery's own clock: its zone's city, else its fixed offset. */
static void recovery_zone_text(const App*a,char*out,size_t size){
    if(recovery_zone(a))copy(out,size,tz_zones[a->recovery_tz].city);else utc_label(a->utc_offset,out,size);
}
/* What the setup card and the review say about the zone. */
static void tz_label(const App*a,char*out,size_t size){
    const TzZone*z=tz_chosen(a);
    if(z){copy(out,size,z->city);return;}
    char zone[48];recovery_zone_text(a,zone,sizeof(zone));snprintf(out,size,T("As in recovery (%s)"),zone);
}
/* A new system takes the chosen city, else the recovery's zone; with neither, its fixed offset. */
static int locale_append_args(const App*a,char**args,int n){
    args[n++]="--locale";args[n++]=(char*)sys_locales[locale_index(a)];
    const TzZone*z=tz_chosen(a);
    if(!z&&recovery_zone(a))z=&tz_zones[a->recovery_tz];
    if(z){args[n++]="--timezone";args[n++]=(char*)z->id;}
    return n;
}
static void locale_summary(const App*a,char*out,size_t size){
    char zone[64];tz_label(a,zone,sizeof(zone));
    snprintf(out,size,T("Language: %s  /  time zone: %s"),sys_locale_names[locale_index(a)],zone);
}
static void render_locale_card(App*a,int y){
    rounded(a,(Rect){28,y,664,238},18,PANEL);text(a,52,y+20,0,ORANGE,"LANGUAGE AND TIME");
    char line[96],zone[64];
    /* Translated before the raw labels: a city and a language's own name stay as they are. */
    tz_label(a,zone,sizeof(zone));
    raw_text(true);
    snprintf(line,sizeof(line),"%s",sys_locale_names[locale_index(a)]);button(a,(Rect){52,y+60,296,80},line,A_LOCALE_OPEN,ui_free(a),false,0);
    button(a,(Rect){372,y+60,296,80},zone,A_TZ_OPEN,ui_free(a),false,0);
    raw_text(false);
    wrapped(a,52,y+160,610,0,MUTED,"Menus and apps of the new system. A city also switches to summer time.",2);
}
static void render_systems_locale(App*a){
    heading(a,"System language","Menus and apps of the new system speak it.",true);
    unsigned int chosen=locale_index(a);
    raw_text(true);
    for(unsigned int i=0;i<SYS_LOCALES;i++)
        button(a,(Rect){28+(int)(i%2)*344,236+(int)(i/2)*96,320,84},sys_locale_names[i],A_LOCALE_ITEM+(int)i,ui_free(a),i==chosen,0);
    raw_text(false);
    wrapped(a,40,236+(int)((SYS_LOCALES+1)/2)*96+16,640,0,MUTED,"By default the language of this recovery.",2);
}
static void render_systems_timezone(App*a){
    heading(a,a->tz_for_recovery?"Recovery time zone":"Time zone","A city keeps the clock right with summer time.",true);
    int picked=a->tz_for_recovery?(recovery_zone(a)?a->recovery_tz:-1):a->systems_tz;
    a->page_scroll=(int64_t)scroll_clamp((double)a->page_scroll,(double)page_max_scroll(a));int y=232-(int)a->page_scroll;
    a->clip_active=true;a->clip=page_viewport(a);
    /* The recovery's own offset, without summer time, stays on top in every region. */
    /* For the recovery itself the top row is a fixed offset; for a new system, the recovery's zone. */
    char zone[48],line[96];
    if(a->tz_for_recovery){utc_label(recovery_offset_now(a),zone,sizeof(zone));snprintf(line,sizeof(line),T("Fixed offset (%s)"),zone);}
    else{recovery_zone_text(a,zone,sizeof(zone));snprintf(line,sizeof(line),T("As in recovery (%s)"),zone);}
    Rect top={28,y,664,92};surface(a,top,22,picked<0?ORANGE_DARK:PANEL,A_TZ_RECOVERY);
    raw_text(true);ellipsis(a,52,y+24,600,1,picked<0?ORANGE:TEXT,line);raw_text(false);
    register_button(a,top,A_TZ_RECOVERY,ui_free(a),0);y+=108;
    int x=28;
    for(unsigned int n=0;n<TZ_REGIONS;n++){
        int w=text_width(tr(tz_region_labels[n]),0)+40;if(x+w>692){x=28;y+=64;}
        Rect r={x,y,w,52};rounded(a,r,26,a->systems_tz_region==n?ORANGE:PANEL);
        centered(a,r,0,a->systems_tz_region==n?ui_theme->hero_text:TEXT,tz_region_labels[n]);
        register_button(a,r,A_TZ_REGION+(int)n,true,0);x+=w+10;
    }
    y+=72;
    for(unsigned int i=0;i<TZ_ZONES;i++){
        const TzZone*z=&tz_zones[i];if(z->region!=a->systems_tz_region)continue;
        Rect r={28,y,664,88};bool on=picked==(int)i;
        surface(a,r,20,on?ORANGE_DARK:PANEL,A_TZ_ITEM+(int)i);
        char offsets[32];tz_zone_offsets(z,offsets,sizeof(offsets));
        raw_text(true);ellipsis(a,52,y+22,400,1,on?ORANGE:TEXT,z->city);ellipsis(a,460,y+28,210,0,MUTED,offsets);raw_text(false);
        register_button(a,r,A_TZ_ITEM+(int)i,ui_free(a),0);y+=98;
    }
    a->page_content=y+a->page_scroll-230+16;a->clip_active=false;scroll_indicator(a,(Rect){698,230,5,1254},a->page_scroll,a->page_content,ORANGE);
}
static bool locale_action(App*a,int id){
    if(a->screen==SYSTEMS_SETUP){
        if(id==A_LOCALE_OPEN){screen(a,SYSTEMS_LOCALE);return true;}
        if(id==A_TZ_OPEN){
            /* The new system's zone, never the recovery's (2026-11-14 review). */
            const TzZone*z=tz_chosen(a);a->tz_for_recovery=false;a->systems_tz_region=z?z->region:0u;a->page_scroll=0;screen(a,SYSTEMS_TIMEZONE);return true;
        }
        return false;
    }
    if(a->screen==SYSTEMS_LOCALE){
        if(id>=A_LOCALE_ITEM&&id<A_LOCALE_ITEM+(int)SYS_LOCALES){a->systems_locale=id-A_LOCALE_ITEM;screen(a,SYSTEMS_SETUP);return true;}
        if(id==A_BACK){screen(a,SYSTEMS_SETUP);return true;}
        return false;
    }
    if(a->screen==SYSTEMS_TIMEZONE&&a->tz_for_recovery){
        if(id>=A_TZ_REGION&&id<A_TZ_REGION+(int)TZ_REGIONS){a->systems_tz_region=(unsigned int)(id-A_TZ_REGION);a->page_scroll=0;a->dirty=true;return true;}
        bool chosen=id==A_TZ_RECOVERY||(id>=A_TZ_ITEM&&id<A_TZ_ITEM+(int)TZ_ZONES);
        if(!chosen&&id!=A_BACK)return false;
        if(id==A_TZ_RECOVERY){a->utc_offset=recovery_offset_now(a);a->recovery_tz=-1;}
        else if(chosen)a->recovery_tz=id-A_TZ_ITEM;
        if(chosen){apply_recovery_tz(a);settings_changed(a);}
        a->tz_for_recovery=false;screen(a,TOOLS);return true;
    }
    if(a->screen==SYSTEMS_TIMEZONE){
        if(id>=A_TZ_REGION&&id<A_TZ_REGION+(int)TZ_REGIONS){a->systems_tz_region=(unsigned int)(id-A_TZ_REGION);a->page_scroll=0;a->dirty=true;return true;}
        if(id==A_TZ_RECOVERY){a->systems_tz=-1;screen(a,SYSTEMS_SETUP);return true;}
        if(id>=A_TZ_ITEM&&id<A_TZ_ITEM+(int)TZ_ZONES){a->systems_tz=id-A_TZ_ITEM;screen(a,SYSTEMS_SETUP);return true;}
        if(id==A_BACK){screen(a,SYSTEMS_SETUP);return true;}
        return false;
    }
    return false;
}

#endif
