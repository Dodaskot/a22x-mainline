/* Charging screen switch (2026-11-09): the installed system's etc/a22x/charger-mode, which BOOT's
   /init reads when LK starts the phone in charge-only mode. Off boots Linux instead of waiting
   in the charger screen for a Power hold, also after a reset that lost the restart markers. */

static bool start_charger_job(App*a,int set){ /* set: 0 read, 1 on, 2 off */
    if(a->job.pid||a->preview||!a->distro_helper[0])return false;
    char*args[10];int n=0;args[n++]=a->python;args[n++]=a->distro_helper;
    if(a->device)args[n++]="--device";else{args[n++]="--fixture";args[n++]=a->fixture;}
    args[n++]="charger-mode";if(set){args[n++]="--set";args[n++]=set==1?"on":"off";}args[n]=NULL;
    a->charger_asked=set;return launch_job(a,J_CHARGER,args);
}
static void complete_charger_job(App*a,const Json*j,bool ok){
    char mode[8]="";if(ok&&j)jstring(j,0,"charger_mode",mode,sizeof(mode));
    a->charger_known=ok&&(!strcmp(mode,"on")||!strcmp(mode,"off"));a->charger_off=a->charger_known&&!strcmp(mode,"off");
    if(!ok&&j){int error=json_get(j,0,"error");char code[48]="",message[200]="";if(error>=0){jstring(j,error,"code",code,sizeof(code));jstring(j,error,"message",message,sizeof(message));}
        /* The usual case: userdata is not mounted. The helper's own text is English and technical. */
        if(!strcmp(code,"VOLUME_NOT_MOUNTED")||!strcmp(code,"VOLUME_READ_ONLY"))copy(a->charger_note,sizeof(a->charger_note),tr(!strcmp(code,"VOLUME_READ_ONLY")?"Enable writes on userdata in Files to change it":"Mount userdata in Files to see and change it"));
        else if(!strcmp(code,"NO_ACTIVE_SYSTEM"))copy(a->charger_note,sizeof(a->charger_note),tr("No installed system on userdata"));
        else copy(a->charger_note,sizeof(a->charger_note),message);}
    else a->charger_note[0]=0;
    if(a->charger_asked&&a->charger_known)toast(a,a->charger_off?"The phone starts Linux when charging":"The charging screen is back");
    a->charger_asked=0;a->dirty=true;
}
static int render_charger_card(App*a,int y,bool free_ui){
    rounded(a,(Rect){28,y,664,200},24,PANEL);icon_tile(a,ICON_DISPLAY,46,y+16,56,ICON_FILL);text(a,120,y+14,1,TEXT,"Charging screen");
    if(a->charger_note[0])raw_text(true);
    ellipsis(a,120,y+52,540,0,a->charger_note[0]?ORANGE:MUTED,a->charger_note[0]?a->charger_note:(!a->charger_known?"Mount userdata to read it":(a->charger_off?"Off: a charger starts Linux":"On: a charger shows the battery")));
    raw_text(false);
    button(a,(Rect){46,y+100,306,72},"On",A_CHARGER_ON,free_ui&&a->charger_known,a->charger_known&&!a->charger_off,0);
    button(a,(Rect){368,y+100,306,72},"Off",A_CHARGER_OFF,free_ui&&a->charger_known,a->charger_known&&a->charger_off,0);
    return 212;
}
