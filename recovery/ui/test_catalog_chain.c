/* SPDX-License-Identifier: MIT — the real job pipeline: the catalog list, then the server check its
   completion starts. A stand-in "python" prints the replies of distro.py (2026-11-08 phone bug). */
#define main recovery_ui_program_main
#include "recovery_ui.c"
#undef main
static const char*list_reply="{\"images\":[{\"id\":\"debian-13\",\"name\":\"Debian\",\"version\":\"13\",\"family\":\"debian\",\"size\":1,\"file\":\"d.qcow2\",\"format\":\"qcow2\",\"minimal\":true,\"unavailable\":false,\"checked\":1,\"signed\":false,\"reachable\":true}],\"https\":true,\"network\":true,\"ok\":true,\"operation\":\"catalog\",\"mode\":\"device\"}";
static const char*check_reply="{\"images\":[{\"id\":\"debian-13\",\"name\":\"Debian\",\"version\":\"13\",\"family\":\"debian\",\"size\":1,\"file\":\"d.qcow2\",\"format\":\"qcow2\",\"minimal\":true,\"unavailable\":false,\"checked\":2,\"signed\":false,\"reachable\":true}],\"https\":true,\"network\":true,\"refresh\":{\"checked\":10,\"updated\":0,\"unavailable\":0,\"failed\":0,\"problems\":{}},\"ok\":true,\"operation\":\"catalog\",\"mode\":\"device\"}";
int main(int argc,char**argv){
    if(argc!=2)return 2;
    char script[PATH_LEN];snprintf(script,sizeof(script),"%s/fake-python",argv[1]);
    FILE*f=fopen(script,"w");if(!f)return 2;
    fprintf(f,"#!/bin/sh\ncase \"$*\" in *--refresh*) echo '%s';; *) echo '%s';; esac\n",check_reply,list_reply);fclose(f);chmod(script,0700);
    App*a=calloc(1,sizeof(*a));if(!a)return 2;initialize(a);a->device=true;copy(a->python,sizeof(a->python),script);copy(a->distro_helper,sizeof(a->distro_helper),"/opt/a22x-recovery/distro.py");
    screen(a,SYSTEMS_CATALOG);if(!start_catalog_job(a))return 3;
    uint64_t deadline=milliseconds()+10000;
    while((a->job.pid||!a->catalog_check_done)&&milliseconds()<deadline){a->now=milliseconds();poll_job(a);struct timespec p={0,1000000};nanosleep(&p,NULL);}
    bool good=a->catalog_check_done&&a->catalog_servers==10&&a->catalog_failed==0&&a->catalog_count==1;
    printf("{\"ok\":%s,\"check_done\":%d,\"servers\":%u,\"failed\":%u}\n",good?"true":"false",a->catalog_check_done,a->catalog_servers,a->catalog_failed);
    free(a->pixels);free(a);return good?0:1;
}
