/* SPDX-License-Identifier: MIT — the shells and sign-in screen of a new system: the way through
   the install pages, which shells can be added, and the arguments distro.py receives. */
#define main recovery_ui_program_main
#include "recovery_ui.c"
#undef main
static bool has_args(char**args,int n,const char*key,const char*value){
    for(int i=0;i+1<n;i++)if(!strcmp(args[i],key)&&!strcmp(args[i+1],value))return true;
    return false;
}
int main(void){
    App*a=calloc(1,sizeof(*a));
    if(!a)
        return 2;
    initialize(a);a->preview=true;
    #define C(n,e) do{if(!(e)){fprintf(stderr,"shells-ui check %d failed: %s\n",n,#e);return n;}}while(0)
    /* Phosh as the main shell: Next leads to the shells page, Back from the network page too. */
    a->systems_desktop=2;a->systems_new_account=false;screen(a,SYSTEMS_SETUP);
    action(a,A_SYS_SETUP_NEXT);
    C(3,a->screen==SYSTEMS_SHELLS);
    render(a);
    Button*b=test_button(a,A_SHELL_ITEM+0);
    C(4,b&&!b->enabled);              /* The main shell is not an extra one. */
    action(a,A_SHELL_ITEM+2);         /* Plasma Mobile */
    action(a,A_SHELL_ITEM+0);         /* Phosh: ignored */
    C(5,shells_extra(a)==(1u<<4));
    action(a,A_GREETER_ITEM+1);       /* Phrog */
    char*args[16];int n=shells_append_args(a,args,0);
    C(6,n==4&&has_args(args,n,"--shell","plasma")&&has_args(args,n,"--greeter","phrog"));
    char line[200];shells_summary(a,line,sizeof(line));
    C(7,strstr(line,"Phrog")&&strstr(line,"Plasma Mobile"));
    action(a,A_SHELLS_NEXT);
    C(8,a->screen==SYSTEMS_EXTRAS);
    action(a,A_BACK);
    C(9,a->screen==SYSTEMS_SHELLS);
    action(a,A_BACK);
    C(10,a->screen==SYSTEMS_SETUP);
    /* Arch from the catalog: Sxmo and Lomiri are Debian's and Ubuntu's only. */
    a->catalog_count=1;copy(a->catalog[0].id,sizeof(a->catalog[0].id),"archlinuxarm");
    copy(a->catalog[0].family,sizeof(a->catalog[0].family),"arch");copy(a->systems_remote_id,sizeof(a->systems_remote_id),"archlinuxarm");
    screen(a,SYSTEMS_SHELLS);render(a);
    b=test_button(a,A_SHELL_ITEM+4);
    C(11,b&&!b->enabled);
    action(a,A_SHELL_ITEM+4);
    C(12,shells_extra(a)==(1u<<4));
    a->systems_shells|=1u<<6;          /* Chosen before the image changed: not sent. */
    C(13,shells_extra(a)==(1u<<4));
    /* Plasma becomes the main shell: it is no longer an extra one. */
    a->systems_desktop=4;
    C(14,shells_extra(a)==0);
    /* Console or the image's own desktop: no shells page and nothing sent. */
    a->systems_desktop=1;screen(a,SYSTEMS_SETUP);action(a,A_SYS_SETUP_NEXT);
    C(15,a->screen==SYSTEMS_EXTRAS);
    C(16,shells_append_args(a,args,0)==0);
    action(a,A_BACK);
    C(17,a->screen==SYSTEMS_SETUP);
    /* A new installation starts from automatic sign-in and no extra shells. */
    systems_forget_choices(a);
    C(18,a->systems_shells==0&&a->systems_greeter==0);
    /* Phrog: only Arch and Manjaro (or a file of unknown family) mention the build on the phone. */
    copy(a->catalog[0].family,sizeof(a->catalog[0].family),"arch");
    C(19,greeter_note(a,1)==greeter_notes[1]);
    copy(a->catalog[0].family,sizeof(a->catalog[0].family),"debian");
    C(20,greeter_note(a,1)==greeter_phrog_packaged&&greeter_note(a,0)==greeter_notes[0]);
    a->systems_remote_id[0]=0;
    C(21,greeter_note(a,1)==greeter_notes[1]);
    /* App icons follow the package the new system gets (2026-11-09). */
    a->systems_remote_id[0]=0;a->systems_desktop=2;  /* Phosh, an image from a file: Debian's choice */
    C(22,app_icon_style(a)==0&&app_icon_family(a)==0);
    int browser=app_icon_index(a,0),files=app_icon_index(a,2);
    C(23,browser>=0&&!strcmp(app_icon_packages[browser],"firefox-esr")&&files>=0&&!strcmp(app_icon_packages[files],"nautilus"));
    C(24,app_icon_index(a,8)<0&&app_symbol(8)==ICON_LINUX);  /* ssh: a service, a symbol */
    copy(a->catalog[0].family,sizeof(a->catalog[0].family),"ubuntu");copy(a->systems_remote_id,sizeof(a->systems_remote_id),"archlinuxarm");
    browser=app_icon_index(a,0);
    C(25,app_icon_family(a)==1&&browser>=0&&!strcmp(app_icon_packages[browser],"epiphany-browser"));
    a->systems_desktop=4;files=app_icon_index(a,2);  /* Plasma Mobile: KDE's apps */
    C(26,app_icon_style(a)==1&&files>=0&&!strcmp(app_icon_packages[files],"dolphin"));
    a->screen=SYSTEMS_APP_LIST;a->systems_apps=1u;render(a);C(27,test_button(a,A_SYS_APP)!=NULL);
    /* A file's family comes from its name (2026-11-09). */
    a->systems_remote_id[0]=0;copy(a->systems_manifest,sizeof(a->systems_manifest),"/Images/ArchLinuxARM-aarch64-latest.tar.gz");
    C(28,!strcmp(systems_image_family(a),"arch")&&greeter_note(a,1)==greeter_notes[1]&&app_icon_family(a)==3);
    copy(a->systems_manifest,sizeof(a->systems_manifest),"/Images/Fedora-Cloud-Base-Generic-45-1.4.aarch64.qcow2");
    C(29,!strcmp(systems_image_family(a),"fedora")&&greeter_note(a,1)==greeter_phrog_packaged&&app_icon_family(a)==2);
    copy(a->systems_manifest,sizeof(a->systems_manifest),"/Images/rootfs.img");
    C(30,systems_image_family(a)==NULL&&greeter_note(a,1)==greeter_notes[1]);
    /* Sxmo and Lomiri only where the family may have them. */
    copy(a->systems_manifest,sizeof(a->systems_manifest),"/Images/Fedora-Cloud-Base-Generic-45-1.4.aarch64.qcow2");
    C(31,!systems_image_debian(a)&&!shell_available(a,6)&&shell_available(a,4));
    copy(a->systems_manifest,sizeof(a->systems_manifest),"/Images/mobian-rockchip-phosh-13.0.img.xz");
    C(32,systems_image_debian(a)&&shell_available(a,6));
    printf("{\"ok\":true,\"checks\":32}\n");free(a->pixels);free(a);return 0;
}
