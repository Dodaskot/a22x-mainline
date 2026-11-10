/* SPDX-License-Identifier: MIT — language and time zone of a new system: the recovery's own by
   default, the pickers, and the arguments distro.py receives. */
#define main recovery_ui_program_main
#include "recovery_ui.c"
#undef main
static bool has_args(char**args,int n,const char*key,const char*value){
    for(int i=0;i+1<n;i++)if(!strcmp(args[i],key)&&!strcmp(args[i+1],value))return true;
    return false;
}
static int zone(const char*id){for(unsigned int i=0;i<TZ_ZONES;i++)if(!strcmp(tz_zones[i].id,id))return (int)i;return -1;}
int main(void){
    App*a=calloc(1,sizeof(*a));
    if(!a)
        return 2;
    initialize(a);a->preview=true;
    #define C(n,e) do{if(!(e)){fprintf(stderr,"locale-ui check %d failed: %s\n",n,#e);return n;}}while(0)
    for(unsigned int n=0;n<LANGUAGE_COUNT;n++)if(!strcmp(language_codes[n],"uk"))ui_language=n;
    C(3,a->systems_locale==-1&&a->systems_tz==-1);
    C(4,!strcmp(sys_locales[locale_index(a)],"uk_UA"));
    char*args[8];int n=locale_append_args(a,args,0);
    C(5,n==2&&has_args(args,n,"--locale","uk_UA"));
    a->systems_desktop=2;screen(a,SYSTEMS_SETUP);render(a);
    action(a,A_LOCALE_OPEN);
    C(6,a->screen==SYSTEMS_LOCALE);
    render(a);
    action(a,A_LOCALE_ITEM+12);
    C(7,a->screen==SYSTEMS_SETUP&&!strcmp(sys_locales[locale_index(a)],"ru_RU"));
    action(a,A_TZ_OPEN);
    C(8,a->screen==SYSTEMS_TIMEZONE&&a->systems_tz_region==0&&page_scrolls(a));
    render(a);
    action(a,A_TZ_REGION+1);
    C(9,a->systems_tz_region==1);
    int ny=zone("America/New_York");
    C(10,ny>=0);
    action(a,A_TZ_ITEM+ny);
    C(11,a->screen==SYSTEMS_SETUP&&tz_chosen(a)==&tz_zones[ny]);
    n=locale_append_args(a,args,0);
    C(12,n==4&&has_args(args,n,"--locale","ru_RU")&&has_args(args,n,"--timezone","America/New_York"));
    char line[200];locale_summary(a,line,sizeof(line));
    C(13,strstr(line,"Русский")&&strstr(line,"New York"));
    /* Reopened, the picker shows the chosen city's region; "as in recovery" drops the city. */
    action(a,A_TZ_OPEN);
    C(14,a->systems_tz_region==1);
    action(a,A_TZ_RECOVERY);
    C(15,a->systems_tz==-1&&locale_append_args(a,args,0)==2);
    a->utc_offset=60;tz_label(a,line,sizeof(line));
    C(16,strstr(line,"UTC+1")!=NULL);
    /* A new installation starts from the recovery's language and clock again. */
    systems_forget_choices(a);
    C(17,a->systems_locale==-1&&a->systems_tz==-1&&a->systems_tz_region==0);
    const TzZone*kyiv=&tz_zones[zone("Europe/Kyiv")];char offsets[32];tz_zone_offsets(kyiv,offsets,sizeof(offsets));
    C(18,!strcmp(offsets,"UTC+2 / +3"));
    /* The recovery clock in a zone keeps summer time (2026-11-09). */
    a->recovery_tz=zone("Europe/Berlin");apply_recovery_tz(a);struct tm tm;
    recovery_time(a,(time_t)1782907200,&tm);C(19,tm.tm_hour==14);   /* 2026-07-01 12:00 UTC, CEST */
    recovery_time(a,(time_t)1768478400,&tm);C(20,tm.tm_hour==13);   /* 2026-01-15 12:00 UTC, CET */
    tz_label(a,line,sizeof(line));C(21,strstr(line,"Berlin")!=NULL);
    n=locale_append_args(a,args,0);C(22,n==4&&has_args(args,n,"--timezone","Europe/Berlin"));
    /* Settings: the city button opens the same picker for the recovery; a city returns to Tools. */
    screen(a,TOOLS);action(a,A_TZ_PICK);C(23,a->screen==SYSTEMS_TIMEZONE&&a->tz_for_recovery&&a->systems_tz_region==0);
    render(a);int kyiv_index=zone("Europe/Kyiv");action(a,A_TZ_ITEM+kyiv_index);
    C(24,a->screen==TOOLS&&!a->tz_for_recovery&&a->recovery_tz==kyiv_index&&a->settings_dirty&&a->systems_tz==-1);
    action(a,A_TZ_PICK);action(a,A_TZ_RECOVERY);
    C(25,a->screen==TOOLS&&a->recovery_tz==-1&&(a->utc_offset==120||a->utc_offset==180));
    /* The fixed offset steps from the zone's offset now. */
    a->recovery_tz=zone("Europe/Berlin");apply_recovery_tz(a);action(a,A_TZ_UP);
    C(26,a->recovery_tz==-1&&(a->utc_offset==90||a->utc_offset==150));
    action(a,A_TZ_PICK);action(a,A_BACK);C(27,a->screen==TOOLS&&!a->tz_for_recovery);
    printf("{\"ok\":true,\"checks\":27,\"zones\":%u}\n",TZ_ZONES);free(a->pixels);free(a);return 0;
}
