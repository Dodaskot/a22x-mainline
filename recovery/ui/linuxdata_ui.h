/* SPDX-License-Identifier: MIT
 * Linux data (2026-11-13, the owner): what is one's own in a Linux system, without the system: home
 * folders, accounts, settings and the list of packages, each chosen by itself, into one archive on a
 * card or a stick (linux_data.py), and from there into whatever Linux is installed next, on this
 * phone or on another device. Not a system kept beside another: Systems does that.
 */
#ifndef A22X_LINUXDATA_UI_H
#define A22X_LINUXDATA_UI_H

static bool ld_job(int kind){return kind==J_LD_INSPECT||kind==J_LD_BACKUP||kind==J_LD_RESTORE;}
static const char*const ld_part_names[LD_PARTS]={"home","accounts","settings","packages"};
static const char*const ld_part_titles[LD_PARTS]={"Home folders","Accounts","Settings","Packages"};
static bool ld_part_usable(const App*a,unsigned int n){
    return a->ld_linux&&(n!=3||a->ld_packages>=0)&&!(n==0&&a->ld_home==UINT64_MAX)&&!(n==2&&a->ld_settings==UINT64_MAX);
}
/* What a backup takes: the owner's choice (ld_parts, never changed by a reading of the system) and
   only the parts that can be backed up now (2026-11-14 reviews: a part the reading had left out
   stayed out, and one that could not be counted kept the button on for a backup that would fail). */
static unsigned int ld_selection(const App*a){
    unsigned int mask=0;for(unsigned int n=0;n<LD_PARTS;n++)if((a->ld_parts>>n&1u)&&ld_part_usable(a,n))mask|=1u<<n;return mask;
}
/* Whether the parts chosen fit in one archive together: homes and settings each fit alone, and the
   backup holds both under one limit (2026-11-14 review: a home that fit alone was hidden). */
static bool ld_fits(const App*a){
    unsigned int chosen=ld_selection(a);
    return !((chosen&1u)&&(chosen&4u))||!a->ld_max_entries||a->ld_home_entries+a->ld_settings_entries<=a->ld_max_entries;
}
static unsigned int ld_mask(const Json*j,int list){
    unsigned int mask=0;if(list<0||j->tokens[list].type!=J_ARRAY)return 0;
    for(int i=list+1;i<json_next(j,list);i=json_next(j,i)){char name[24];if(json_string(j,i,name,sizeof(name))<=0)continue;
        for(unsigned int n=0;n<LD_PARTS;n++)if(!strcmp(name,ld_part_names[n]))mask|=1u<<n;}
    return mask;
}
static void ld_names(const Json*j,int list,char*out,size_t size){
    out[0]=0;if(list<0||j->tokens[list].type!=J_ARRAY)return;
    for(int i=list+1;i<json_next(j,list);i=json_next(j,i)){char name[48];if(json_string(j,i,name,sizeof(name))<=0)continue;if(out[0])append(out,size,", ");append(out,size,name);}
}
static void ld_parts_argument(unsigned int mask,char*out,size_t size){
    out[0]=0;for(unsigned int n=0;n<LD_PARTS;n++)if(mask&(1u<<n)){if(out[0])append(out,size,",");append(out,size,ld_part_names[n]);}
}
static bool start_ld_job(App*a,int kind){
    if(a->job.pid||a->preview||!a->filesystem_helper[0])return false;
    static char helper[PATH_LEN],parts[64];
    copy(helper,sizeof(helper),a->filesystem_helper);char*slash=strrchr(helper,'/');if(slash)slash[1]=0;else helper[0]=0;append(helper,sizeof(helper),"linux_data.py");
    char*args[24];int n=0;args[n++]=a->python;args[n++]=helper;
    if(a->device)args[n++]="--device";else{args[n++]="--fixture";args[n++]=a->fixture;}
    if(a->backup_password[0]&&a->backup_password_file[0]){args[n++]="--password-file";args[n++]=a->backup_password_file;}
    if(kind==J_LD_INSPECT)args[n++]="inspect";
    else if(kind==J_LD_BACKUP){
        if(a->ld_dest_index<0||a->ld_dest_index>=(int)a->ld_dest_count||!ld_selection(a)||!ld_fits(a))return false;
        ld_parts_argument(ld_selection(a),parts,sizeof(parts));
        args[n++]="backup";args[n++]="--volume";args[n++]=a->ld_dest[a->ld_dest_index].id;args[n++]="--parts";args[n++]=parts;args[n++]="--confirm";
        if(a->backup_encrypt&&a->backup_password[0])args[n++]="--encrypt";
    }else{
        if(a->ld_selected<0||a->ld_selected>=(int)a->ld_archive_count||!a->ld_restore_parts)return false;
        LdArchive*x=&a->ld_archives[a->ld_selected];ld_parts_argument(a->ld_restore_parts,parts,sizeof(parts));
        args[n++]="restore";args[n++]="--volume";args[n++]=x->volume;args[n++]="--expected-volume-token";args[n++]=x->token;
        args[n++]="--path";args[n++]=x->path;args[n++]="--parts";args[n++]=parts;args[n++]="--confirm";
    }
    args[n]=NULL;return launch_job(a,kind,args);
}
static void complete_ld_job(App*a,const Json*j,int kind,bool ok){
    if(!ok||!j){
        char code[64]="LINUX_DATA_FAILED",message[320]="The Linux data helper did not return a result.";
        if(j){int error=json_get(j,0,"error");if(error>=0&&j->tokens[error].type==J_OBJECT){jstring(j,error,"code",code,sizeof(code));jstring(j,error,"message",message,sizeof(message));}}
        if(kind==J_LD_INSPECT)a->ld_known=true;
        files_problem(a,code,message,LINUX_DATA);return;
    }
    if(kind==J_LD_INSPECT){
        a->ld_linux=json_bool(j,json_get(j,0,"linux"),0)==1;a->ld_os[0]=a->ld_accounts[0]=0;a->ld_home=a->ld_settings=0;a->ld_account_count=0;a->ld_packages=-1;
        jstring(j,0,"reason",a->ld_reason,sizeof(a->ld_reason));
        if(a->ld_linux){
            int os=json_get(j,0,"os"),parts=json_get(j,0,"parts");if(os>=0)jstring(j,os,"name",a->ld_os,sizeof(a->ld_os));
            ld_names(j,json_get(j,0,"accounts"),a->ld_accounts,sizeof(a->ld_accounts));
            /* -1: not counted (a home too large to walk; 2026-11-14 review), kept as UINT64_MAX. */
            if(parts>=0){int home=json_get(j,parts,"home"),settings=json_get(j,parts,"settings");
                a->ld_home=home>=0&&j->text[j->tokens[home].start]=='-'?UINT64_MAX:json_uint(j,home,0);
                a->ld_settings=settings>=0&&j->text[j->tokens[settings].start]=='-'?UINT64_MAX:json_uint(j,settings,0);
                a->ld_account_count=(unsigned int)json_uint(j,json_get(j,parts,"accounts"),0);
                int packages=json_get(j,parts,"packages");a->ld_packages=packages>=0&&j->tokens[packages].type==J_PRIMITIVE&&j->text[j->tokens[packages].start]!='-'?(int)json_uint(j,packages,0):-1;}
            int entries=json_get(j,0,"entries");a->ld_max_entries=json_uint(j,json_get(j,0,"max_entries"),0);
            a->ld_home_entries=entries>=0?json_uint(j,json_get(j,entries,"home"),0):0;a->ld_settings_entries=entries>=0?json_uint(j,json_get(j,entries,"settings"),0):0;
        }
        a->ld_dest_count=0;int list=json_get(j,0,"destinations");
        if(list>=0&&j->tokens[list].type==J_ARRAY)for(int i=list+1;i<json_next(j,list)&&a->ld_dest_count<LD_DESTS;i=json_next(j,i)){
            LdDest*d=&a->ld_dest[a->ld_dest_count++];jstring(j,i,"volume",d->id,sizeof(d->id));jstring(j,i,"label",d->label,sizeof(d->label));d->free=json_uint(j,json_get(j,i,"free_bytes"),0);}
        if(a->ld_dest_index>=(int)a->ld_dest_count)a->ld_dest_index=0;
        /* The page of the list stays: a password typed for a backup on page 2 left the page 1 up
           (2026-11-14 review); it starts at 1 when the page is opened (A_LD). */
        a->ld_archive_count=0;list=json_get(j,0,"archives");
        if(list>=0&&j->tokens[list].type==J_ARRAY)for(int i=list+1;i<json_next(j,list)&&a->ld_archive_count<LD_ARCHIVES;i=json_next(j,i)){
            LdArchive*x=&a->ld_archives[a->ld_archive_count++];memset(x,0,sizeof(*x));
            jstring(j,i,"volume",x->volume,sizeof(x->volume));jstring(j,i,"volume_token",x->token,sizeof(x->token));jstring(j,i,"path",x->path,sizeof(x->path));
            jstring(j,i,"name",x->name,sizeof(x->name));jstring(j,i,"label",x->label,sizeof(x->label));
            int os=json_get(j,i,"os");if(os>=0)jstring(j,os,"name",x->os,sizeof(x->os));
            x->bytes=json_uint(j,json_get(j,i,"bytes"),0);x->created=json_uint(j,json_get(j,i,"created"),0);x->packages=(unsigned int)json_uint(j,json_get(j,i,"packages"),0);
            x->parts=ld_mask(j,json_get(j,i,"parts"));ld_names(j,json_get(j,i,"accounts"),x->accounts,sizeof(x->accounts));
            x->locked=json_bool(j,json_get(j,i,"locked"),0)==1;x->encrypted=json_bool(j,json_get(j,i,"encrypted"),0)==1;x->wrong=json_bool(j,json_get(j,i,"wrong_password"),0)==1;}
        a->ld_known=true;a->dirty=true;if(a->screen==PROGRESS)screen(a,LINUX_DATA);return;
    }
    a->successful=true;a->result_return=LINUX_DATA;a->ld_known=false;
    if(kind==J_LD_BACKUP){
        char where[PATH_LEN]={0},bytes[48];int archive=json_get(j,0,"archive");
        if(archive>=0){jstring(j,archive,"path",where,sizeof(where));human_bytes(json_uint(j,json_get(j,archive,"bytes"),0),bytes,sizeof(bytes));}else bytes[0]=0;
        bool protected_copy=json_bool(j,json_get(j,0,"encrypted"),0)==1;
        copy(a->status,sizeof(a->status),protected_copy?"Linux data saved, protected with the password":"Linux data saved");
        unsigned int skipped=(unsigned int)json_uint(j,json_get(j,0,"skipped"),0);   /* device nodes (2026-11-14 review) */
        if(skipped){   /* the status is translated as a whole when drawn: translated here, then the note */
            char base[sizeof(a->status)],note[96];copy(base,sizeof(base),tr(a->status));
            snprintf(note,sizeof(note),T("; %u device files left out"),skipped);snprintf(a->status,sizeof(a->status),"%s%s",base,note);}
        snprintf(a->message,sizeof(a->message),T("The archive is %s, %s.\n\nIt restores into any Linux installed here from this page, and on another device with the restore.sh inside it."),where,bytes);
        screen(a,RESULT);return;
    }
    char created[160],kept[160],missing[400],into[96]={0},note[200]={0},line[320];
    ld_names(j,json_get(j,0,"accounts_created"),created,sizeof(created));ld_names(j,json_get(j,0,"accounts_kept"),kept,sizeof(kept));ld_names(j,json_get(j,0,"missing"),missing,sizeof(missing));
    int target=json_get(j,0,"into");if(target>=0)jstring(j,target,"name",into,sizeof(into));jstring(j,0,"note",note,sizeof(note));
    unsigned int installed=(unsigned int)json_uint(j,json_get(j,0,"packages_installed"),0),lost=(unsigned int)json_uint(j,json_get(j,0,"packages_missing"),0);
    copy(a->status,sizeof(a->status),"Linux data restored");
    snprintf(a->message,sizeof(a->message),T("Restored into %s."),into);append(a->message,sizeof(a->message),"\n\n");
    if(created[0]){snprintf(line,sizeof(line),T("Accounts made: %s"),created);append(a->message,sizeof(a->message),line);append(a->message,sizeof(a->message),"\n");}
    if(kept[0]){snprintf(line,sizeof(line),T("Accounts already there: %s"),kept);append(a->message,sizeof(a->message),line);append(a->message,sizeof(a->message),"\n");}
    if(a->ld_restore_parts&8u){
        if(json_bool(j,json_get(j,0,"packages_unsupported"),0)==1)append(a->message,sizeof(a->message),tr("This system's package manager is not known here: the packages were not installed."));
        else{snprintf(line,sizeof(line),T("Packages there: %u; not in this system: %u"),installed,lost);append(a->message,sizeof(a->message),line);}
        append(a->message,sizeof(a->message),"\n");
        /* Left out because the system could not be upgraded first: counted apart (2026-11-14 review). */
        unsigned int skipped=(unsigned int)json_uint(j,json_get(j,0,"packages_skipped"),0);
        if(skipped){snprintf(line,sizeof(line),T("Packages not installed now: %u"),skipped);append(a->message,sizeof(a->message),line);append(a->message,sizeof(a->message),"\n");}
        if(missing[0]){append(a->message,sizeof(a->message),missing);append(a->message,sizeof(a->message),"\n");}
    }
    if(note[0])append(a->message,sizeof(a->message),tr(note));
    screen(a,RESULT);
}
static void ld_row(App*a,int y,const char*title,const char*detail,bool on,bool enabled,int action){
    Rect r={28,y,664,76};rounded(a,r,20,on&&enabled?PANEL2:PANEL);
    int cx=66,cy=y+38;
    if(on&&enabled){vector_circle(a,cx,cy,16,16,ORANGE);vector_line(a,cx-7,cy,cx-2,cy+6,3,ON_ORANGE);vector_line(a,cx-2,cy+6,cx+8,cy-6,3,ON_ORANGE);}
    else vector_circle(a,cx,cy,16,3,LINE);
    text(a,104,y+20,1,enabled?TEXT:MUTED,title);
    raw_text(true);int w=text_width(detail,0);text(a,672-w,y+24,0,MUTED,detail);raw_text(false);
    register_button(a,r,action,enabled&&ui_free(a),0);
}
static void render_linux_data(App*a){
    heading(a,"Linux data","Your own files and settings, without the system.",true);
    if(!a->ld_known){text_tile(a,240,1,MUTED,a->job.pid?"Reading the system on userdata...":"Tap Refresh to read the system on userdata.",2);
        button(a,(Rect){28,1340,664,84},"Refresh",A_LD_REFRESH,ui_free(a),false,0);return;}
    rounded(a,(Rect){28,236,664,110},24,PANEL);icon_tile(a,ICON_LINUX,46,256,64,ICON_FILL);
    if(a->ld_linux){raw_text(true);ellipsis(a,128,252,540,1,TEXT,a->ld_os[0]?a->ld_os:"Linux");ellipsis(a,128,298,540,0,MUTED,a->ld_accounts[0]?a->ld_accounts:"-");raw_text(false);}
    else if(!a->ld_reason[0]||!strcmp(a->ld_reason,"NO_LINUX")){text(a,128,252,1,TEXT,"No Linux system on userdata");ellipsis(a,128,298,540,0,MUTED,"A backup can still be restored once one is installed");}
    else{
        /* Why the system could not be read, not a silent no-system line (2026-11-14 review). */
        char why[160];snprintf(why,sizeof(why),T("Reason: %s"),a->ld_reason);
        text(a,128,252,1,TEXT,"The system on userdata could not be read");raw_text(true);ellipsis(a,128,298,540,0,RED,why);raw_text(false);
    }
    char detail[LD_PARTS][64],bytes[48];
    /* The count is said to be too large only when that is the reason; any other reason is shown (2026-11-14 review). */
    if(a->ld_home==UINT64_MAX)copy(detail[0],sizeof(detail[0]),!strcmp(a->ld_reason,"SELECTION_TOO_LARGE")?tr("too many files to count"):(a->ld_reason[0]?a->ld_reason:"—"));else{human_bytes(a->ld_home,bytes,sizeof(bytes));snprintf(detail[0],sizeof(detail[0]),"%s",bytes);}
    snprintf(detail[1],sizeof(detail[1]),"%u",a->ld_account_count);
    if(a->ld_settings==UINT64_MAX)copy(detail[2],sizeof(detail[2]),"—");else{human_bytes(a->ld_settings,bytes,sizeof(bytes));snprintf(detail[2],sizeof(detail[2]),"%s",bytes);}
    if(a->ld_packages>=0)snprintf(detail[3],sizeof(detail[3]),"%d",a->ld_packages);else copy(detail[3],sizeof(detail[3]),"—");
    for(unsigned int n=0;n<LD_PARTS;n++)ld_row(a,362+(int)n*86,ld_part_titles[n],a->ld_linux?detail[n]:"",(ld_selection(a)>>n)&1u,ld_part_usable(a,n),A_LD_PART+(int)n);
    char label[200];
    if(a->ld_dest_count){LdDest*d=&a->ld_dest[a->ld_dest_index];human_bytes(d->free,bytes,sizeof(bytes));snprintf(label,sizeof(label),T("To: %.100s  /  %s free"),d->label,bytes);}
    else copy(label,sizeof(label),tr("Insert an SD card or attach a USB drive"));
    raw_text(true);button(a,(Rect){28,722,664,72},label,A_LD_DEST,ui_free(a)&&a->ld_dest_count>1,false,0);raw_text(false);
    bool fits=ld_fits(a);
    button(a,(Rect){28,806,664,92},fits?"Hold 2 seconds: back up":"Too many files together: choose fewer parts",A_LD_BACKUP,ui_free(a)&&a->ld_linux&&ld_selection(a)&&fits&&a->ld_dest_count>0,true,2000);
    text(a,34,918,0,ORANGE,"BACKUPS ON CARDS AND STICKS");
    if(!a->ld_archive_count)text(a,34,962,0,MUTED,"None found");
    /* 2026-11-13 review: only the first three were drawn; the rest are on further pages. */
    unsigned int pages=(a->ld_archive_count+LD_ROWS-1)/LD_ROWS;if(pages&&a->ld_page>=pages)a->ld_page=pages-1;
    for(unsigned int n=0;n<LD_ROWS&&a->ld_page*LD_ROWS+n<a->ld_archive_count;n++){
        LdArchive*x=&a->ld_archives[a->ld_page*LD_ROWS+n];Rect r={28,954+(int)n*112,664,100};rounded(a,r,20,PANEL);
        char when[48]="",size[48],line[200];time_t t=(time_t)x->created;struct tm local;if(x->created&&localtime_r(&t,&local))strftime(when,sizeof(when),"%Y-%m-%d %H:%M",&local);
        human_bytes(x->bytes,size,sizeof(size));snprintf(line,sizeof(line),"%s  /  %s  /  %.60s",when,size,x->locked?tr(x->wrong?"another password":"protected: tap to open"):x->label);
        raw_text(true);ellipsis(a,52,r.y+14,580,1,TEXT,x->os[0]?x->os:x->name);ellipsis(a,52,r.y+58,580,0,x->locked?ORANGE:MUTED,line);raw_text(false);
        icon(a,x->encrypted?ICON_LOCK:ICON_CHEVRON,640,r.y+34,32,MUTED);register_button(a,r,A_LD_ARCHIVE+(int)n,ui_free(a),0);
    }
    if(pages>1){
        raw_text(true);button(a,(Rect){28,1340,120,84},"‹",A_LD_PREV,a->ld_page>0,false,0);
        button(a,(Rect){572,1340,120,84},"›",A_LD_NEXT,a->ld_page+1<pages,false,0);raw_text(false);
        button(a,(Rect){164,1340,392,84},"Refresh",A_LD_REFRESH,ui_free(a),false,0);
    }else button(a,(Rect){28,1340,664,84},"Refresh",A_LD_REFRESH,ui_free(a),false,0);
}
static void render_linux_data_restore(App*a){
    heading(a,"Restore Linux data","Nothing has been changed yet.",true);
    if(a->ld_selected<0||a->ld_selected>=(int)a->ld_archive_count)return;
    LdArchive*x=&a->ld_archives[a->ld_selected];
    rounded(a,(Rect){28,236,664,196},24,PANEL);text(a,52,252,0,ORANGE,"FROM");
    char when[48]="",size[48],line[240];time_t t=(time_t)x->created;struct tm local;if(x->created&&localtime_r(&t,&local))strftime(when,sizeof(when),"%Y-%m-%d %H:%M",&local);
    human_bytes(x->bytes,size,sizeof(size));snprintf(line,sizeof(line),"%s  /  %s  /  %.60s",when,size,x->label);
    raw_text(true);ellipsis(a,52,290,616,1,TEXT,x->os[0]?x->os:x->name);ellipsis(a,52,338,616,0,MUTED,line);ellipsis(a,52,378,616,0,MUTED,x->accounts);raw_text(false);
    rounded(a,(Rect){28,448,664,104},24,PANEL);text(a,52,464,0,ORANGE,"INTO");
    raw_text(true);ellipsis(a,52,500,616,1,a->ld_linux?TEXT:RED,a->ld_linux?(a->ld_os[0]?a->ld_os:"Linux"):tr("No Linux system on userdata"));raw_text(false);
    int y=568;
    for(unsigned int n=0;n<LD_PARTS;n++){
        if(!(x->parts&(1u<<n)))continue;
        char detail[32]="";if(n==3)snprintf(detail,sizeof(detail),"%u",x->packages);
        ld_row(a,y,ld_part_titles[n],detail,(a->ld_restore_parts>>n)&1u,a->ld_linux,A_LD_RPART+(int)n);y+=86;
    }
    text_tile(a,y+8,0,MUTED,"Files of the same name are replaced; nothing else in the system is removed. Accounts that are missing are made with their passwords. Packages are installed over Wi-Fi under the names this system gives them.",6);
    button(a,(Rect){28,1320,664,104},"Hold 3 seconds: restore",A_LD_RESTORE,ui_free(a)&&a->ld_linux&&(a->ld_restore_parts&x->parts),true,3000);
}
static bool ld_action(App*a,int id){
    if(id==A_LD){if(ui_free(a)){a->ld_known=false;a->ld_page=0;if(!a->ld_parts)a->ld_parts=15u;screen(a,LINUX_DATA);start_ld_job(a,J_LD_INSPECT);}return true;}
    if(a->screen==LINUX_DATA){
        if(id==A_BACK){reopen(a,came_from(a,BACKUP));return true;}
        if(id==A_LD_REFRESH){if(ui_free(a)){a->ld_known=false;start_ld_job(a,J_LD_INSPECT);a->dirty=true;}return true;}
        if(id>=A_LD_PART&&id<A_LD_PART+(int)LD_PARTS){if(ui_free(a)&&ld_part_usable(a,(unsigned int)(id-A_LD_PART))){a->ld_parts^=1u<<(id-A_LD_PART);a->dirty=true;}return true;}
        if(id==A_LD_DEST){if(ui_free(a)&&a->ld_dest_count){a->ld_dest_index=(a->ld_dest_index+1)%(int)a->ld_dest_count;a->dirty=true;}return true;}
        if(id==A_LD_BACKUP){if(ui_free(a)&&!start_ld_job(a,J_LD_BACKUP))files_problem(a,"LINUX_DATA_UNAVAILABLE","Wait for the current operation to finish.",LINUX_DATA);return true;}
        if(id==A_LD_PREV||id==A_LD_NEXT){
            if(id==A_LD_PREV&&a->ld_page>0)a->ld_page--;
            else if(id==A_LD_NEXT&&(a->ld_page+1)*LD_ROWS<a->ld_archive_count)a->ld_page++;
            a->dirty=true;return true;
        }
        unsigned int at=a->ld_page*LD_ROWS+(unsigned int)(id-A_LD_ARCHIVE);
        if(id>=A_LD_ARCHIVE&&id<A_LD_ARCHIVE+(int)LD_ROWS&&at<a->ld_archive_count&&a->ld_archives[at].locked){
            /* Protected: the password is asked, and the list is read again with it. */
            if(ui_free(a))backup_password_ask(a,J_LD_INSPECT,a->ld_archives[at].wrong,LINUX_DATA);
            return true;
        }
        if(id>=A_LD_ARCHIVE&&id<A_LD_ARCHIVE+(int)LD_ROWS&&at<a->ld_archive_count){if(ui_free(a)){a->ld_selected=(int)at;a->ld_restore_parts=a->ld_archives[at].parts;screen(a,LINUX_DATA_RESTORE);}return true;}
    }
    if(a->screen==LINUX_DATA_RESTORE){
        if(id==A_BACK){screen(a,LINUX_DATA);return true;}
        if(id>=A_LD_RPART&&id<A_LD_RPART+(int)LD_PARTS){if(ui_free(a)){a->ld_restore_parts^=1u<<(id-A_LD_RPART);a->dirty=true;}return true;}
        if(id==A_LD_RESTORE){if(ui_free(a)&&!start_ld_job(a,J_LD_RESTORE))files_problem(a,"LINUX_DATA_UNAVAILABLE","Wait for the current operation to finish.",LINUX_DATA_RESTORE);return true;}
    }
    return false;
}
#endif
