/* SPDX-License-Identifier: MIT — the charging screen switch through the real job pipeline; a
   stand-in "python" answers as distro.py charger-mode does and logs the arguments it got. */
#define main recovery_ui_program_main
#include "recovery_ui.c"
#undef main
static void settle(App*a){uint64_t d=milliseconds()+10000;while(a->job.pid&&milliseconds()<d){a->now=milliseconds();poll_job(a);struct timespec p={0,1000000};nanosleep(&p,NULL);}}
int main(int argc,char**argv){
    if(argc!=2)
        return 2;
    char script[PATH_LEN],log[PATH_LEN],state[PATH_LEN];snprintf(script,sizeof(script),"%s/fake-python",argv[1]);snprintf(log,sizeof(log),"%s/args",argv[1]);snprintf(state,sizeof(state),"%s/mode",argv[1]);
    FILE*f=fopen(script,"w");
    if(!f)return 2;
    fprintf(f,"#!/bin/sh\necho \"$*\" >> %s\ncase \"$*\" in *'--set on'*) echo on > %s;; *'--set off'*) echo off > %s;; esac\nm=$(cat %s 2>/dev/null || echo on)\necho \"{\\\"ok\\\":true,\\\"operation\\\":\\\"charger-mode\\\",\\\"charger_mode\\\":\\\"$m\\\"}\"\n",log,state,state,state);
    fclose(f);
    chmod(script,0700);
    App*a=calloc(1,sizeof(*a));
    if(!a)
        return 2;
    initialize(a);a->device=true;copy(a->python,sizeof(a->python),script);copy(a->distro_helper,sizeof(a->distro_helper),"/opt/a22x-recovery/distro.py");
    #define C(n,e) do{if(!(e)){fprintf(stderr,"charger-ui check %d failed: %s\n",n,#e);return n;}}while(0)
    screen(a,TOOLS);render(a);
    Button*b=test_button(a,A_CHARGER_OFF);
    C(3,b&&!b->enabled);   /* Unknown until read. */
    C(4,start_charger_job(a,0));
    settle(a);
    C(5,a->charger_known&&!a->charger_off);
    render(a);b=test_button(a,A_CHARGER_OFF);
    C(6,b&&b->enabled);
    action(a,A_CHARGER_OFF);
    settle(a);
    C(7,a->charger_known&&a->charger_off);
    action(a,A_CHARGER_ON);
    settle(a);
    C(8,a->charger_known&&!a->charger_off);
    printf("{\"ok\":true}\n");free(a->pixels);free(a);return 0;
}
