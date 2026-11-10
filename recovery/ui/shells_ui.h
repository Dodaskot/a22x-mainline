/* SPDX-License-Identifier: MIT
 * More shells beside the main one, and the sign-in screen that switches between them
 * (2026-11-09). distro.py checks the choice; a22x-firstboot.sh installs the shells and the
 * sign-in screen, and falls back to automatic sign-in when a screen cannot be set up.
 */
#ifndef A22X_SHELLS_UI_H
#define A22X_SHELLS_UI_H

/* The shells are the desktops after "As in image" and "Console" (systems_desktops[2..7]). */
#define SHELLS_FIRST 2u
#define SHELLS_COUNT (SYSTEMS_DESKTOPS-SHELLS_FIRST)
static const char*const greeter_ids[]={"auto","phrog","text","gdm","sddm","lightdm"};
static const char*const greeter_labels[]={"Automatic sign-in","Phrog","Text login","GDM","SDDM","LightDM"};
static const char*const greeter_notes[]={
    "The main shell starts signed in; its lock screen asks for the password. Each shell gets a launcher to switch to it.",
    "A touch login made of Phosh, with the on-screen keyboard and a list of the shells. Arch builds it on the phone: 30-40 minutes more.",
    "A text login for a USB or Bluetooth keyboard. It starts the shell picked last.",
    "GNOME's login screen with its on-screen keyboard.",
    "KDE's login screen with Qt's virtual keyboard.",
    "An X11 login screen with an on-screen keyboard, made for Xfce."};
#define GREETERS 6u
/* Phrog is a package almost everywhere; only Arch (and Manjaro without it) builds it on the phone. */
static const char*const greeter_phrog_packaged="A touch login made of Phosh, with the on-screen keyboard and a list of the shells.";
static const char*greeter_note(const App*a,unsigned int g){
    const char*family=systems_image_family(a);
    if(g==1u&&family&&strcmp(family,"arch")&&strcmp(family,"manjaro"))return greeter_phrog_packaged;
    return greeter_notes[g<GREETERS?g:0u];
}

/* Sxmo and Lomiri only where Debian or Ubuntu packages them. */
static bool shell_available(const App*a,unsigned int n){
    return n>=SHELLS_FIRST&&n<SYSTEMS_DESKTOPS&&(systems_image_debian(a)||!systems_desktop_debian(n));
}
static bool shells_offered(const App*a){return a->systems_desktop>=SHELLS_FIRST&&a->systems_desktop<SYSTEMS_DESKTOPS;}
static unsigned int shells_extra(const App*a){
    unsigned int mask=0;if(!shells_offered(a))return 0;
    for(unsigned int n=SHELLS_FIRST;n<SYSTEMS_DESKTOPS;n++)if(((a->systems_shells>>n)&1u)&&n!=a->systems_desktop&&shell_available(a,n))mask|=1u<<n;
    return mask;
}
static unsigned int shells_greeter(const App*a){return shells_offered(a)&&a->systems_greeter<GREETERS?a->systems_greeter:0u;}
static int shells_append_args(const App*a,char**args,int n){
    unsigned int mask=shells_extra(a);
    for(unsigned int i=SHELLS_FIRST;i<SYSTEMS_DESKTOPS;i++)if((mask>>i)&1u){args[n++]="--shell";args[n++]=(char*)systems_desktops[i];}
    if(shells_greeter(a)){args[n++]="--greeter";args[n++]=(char*)greeter_ids[shells_greeter(a)];}
    return n;
}
/* The sign-in screen and the added shells, for the review. */
static void shells_summary(const App*a,char*out,size_t size){
    char also[160]={0};unsigned int mask=shells_extra(a);size_t used=0;
    for(unsigned int i=SHELLS_FIRST;i<SYSTEMS_DESKTOPS;i++)if((mask>>i)&1u){
        int w=snprintf(also+used,sizeof(also)-used,"%s%s",used?", ":"",tr(systems_desktop_labels[i]));
        if(w<0||(size_t)w>=sizeof(also)-used)break;
        used+=(size_t)w;
    }
    snprintf(out,size,T("Sign-in: %s  /  also: %s"),tr(greeter_labels[shells_greeter(a)]),used?also:"—");
}
static void render_systems_shells(App*a){
    heading(a,"Shells and sign-in","Add shells and choose how the phone signs in.",true);
    rounded(a,(Rect){28,226,664,372},18,PANEL);text(a,52,246,0,ORANGE,"ALSO INSTALL");
    char line[160];snprintf(line,sizeof(line),T("Main shell: %s"),tr(systems_desktop_labels[a->systems_desktop<SYSTEMS_DESKTOPS?a->systems_desktop:0]));
    ellipsis(a,52,282,610,0,MUTED,line);
    unsigned int mask=shells_extra(a);
    for(unsigned int i=0;i<SHELLS_COUNT;i++){
        unsigned int n=SHELLS_FIRST+i;bool main_shell=n==a->systems_desktop;
        button(a,(Rect){52+(int)(i%3)*212,330+(int)(i/3)*88,192,76},systems_desktop_labels[n],A_SHELL_ITEM+(int)i,
               ui_free(a)&&shell_available(a,n)&&!main_shell,main_shell||((mask>>n)&1u),0);
    }
    wrapped(a,52,512,610,0,MUTED,systems_image_debian(a)?"Each shell is installed with its packages; the sign-in screen switches between them.":
            "Each shell is installed with its packages. Sxmo and Lomiri come from Debian and Ubuntu.",2);
    rounded(a,(Rect){28,614,664,500},18,PANEL);text(a,52,634,0,ORANGE,"SIGN-IN SCREEN");
    unsigned int chosen=shells_greeter(a);
    for(unsigned int g=0;g<GREETERS;g++)
        button(a,(Rect){52+(int)(g%2)*320,676+(int)(g/2)*88,296,76},greeter_labels[g],A_GREETER_ITEM+(int)g,ui_free(a),g==chosen,0);
    int note=wrapped(a,52,948,610,0,TEXT,greeter_note(a,chosen),4);
    /* GNOME Shell disables its lock screen unless GDM started the session ("Screen Lock disabled:
       Screen Locking requires the GNOME display manager", Ubuntu 26.04 with SDDM, 2026-11-09). */
    bool gnome=a->systems_desktop==3u||((shells_extra(a)>>3)&1u);
    if(gnome&&chosen!=3u)wrapped(a,52,948+note+8,610,0,ORANGE,"GNOME locks its screen only when GDM signs in.",2);
    button(a,(Rect){28,1330,664,108},"Next",A_SHELLS_NEXT,ui_free(a),true,0);
}
static bool shells_action(App*a,int id){
    if(a->screen!=SYSTEMS_SHELLS)return false;
    if(id>=A_SHELL_ITEM&&id<A_SHELL_ITEM+(int)SHELLS_COUNT){
        unsigned int n=SHELLS_FIRST+(unsigned int)(id-A_SHELL_ITEM);
        if(shell_available(a,n)&&n!=a->systems_desktop){a->systems_shells^=1u<<n;a->dirty=true;}
        return true;
    }
    if(id>=A_GREETER_ITEM&&id<A_GREETER_ITEM+(int)GREETERS){a->systems_greeter=(unsigned int)(id-A_GREETER_ITEM);a->dirty=true;return true;}
    if(id==A_SHELLS_NEXT){screen(a,SYSTEMS_EXTRAS);return true;}
    if(id==A_BACK){screen(a,SYSTEMS_SETUP);return true;}
    return false;
}

#endif
