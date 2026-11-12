/* SPDX-License-Identifier: MIT
 * Partition a card, as TWRP's Partition SD card (2026-11-11): sdcard.py lists the card in the
 * slot and USB drives, and writes a new table with a FAT32 partition for files and, on request,
 * ext4 for Linux and swap. The page shows what the card holds now, since all of it is lost, and
 * nothing is written before the hold. The sizes offered here are checked again by the helper.
 */
#ifndef A22X_CARD_UI_H
#define A22X_CARD_UI_H

static const unsigned int card_linux_gib[]={0,8,16,32,64},card_swap_gib[]={0,1,2,4};
#define CARD_LINUX_CHOICES (sizeof(card_linux_gib)/sizeof(card_linux_gib[0]))
#define CARD_SWAP_CHOICES (sizeof(card_swap_gib)/sizeof(card_swap_gib[0]))

static bool card_job(int kind){return kind==J_CARD_LIST||kind==J_CARD_APPLY;}
static CardDisk*card_current(App*a){return a->card_index>=0&&a->card_index<(int)a->card_count?&a->cards[a->card_index]:NULL;}
/* What is left for files, as sdcard.py lays the card out; 0 when the choice does not fit. */
static uint64_t card_files_bytes(const CardDisk*c,unsigned int linux_gib,unsigned int swap_gib){
    uint64_t taken=c->align+((uint64_t)linux_gib<<30)+((uint64_t)swap_gib<<30);
    return c->usable>=taken+c->min_files?c->usable-taken:0;
}
static bool start_card_job(App*a,int kind){
    if(a->job.pid||a->preview||!a->card_helper[0])return false;
    char*args[16];int n=0;args[n++]=a->python;args[n++]=a->card_helper;
    if(a->device)args[n++]="--device";else{args[n++]="--fixture";args[n++]=a->fixture;}
    if(kind==J_CARD_LIST)args[n++]="list";
    else{
        CardDisk*c=card_current(a);
        if(!c||c->busy_code[0]||!card_files_bytes(c,a->card_linux,a->card_swap))return false;
        snprintf(a->card_linux_arg,sizeof(a->card_linux_arg),"%u",a->card_linux*1024u);snprintf(a->card_swap_arg,sizeof(a->card_swap_arg),"%u",a->card_swap*1024u);
        args[n++]="apply";args[n++]="--disk";args[n++]=c->id;args[n++]="--expected-token";args[n++]=c->token;
        args[n++]="--linux-mib";args[n++]=a->card_linux_arg;args[n++]="--swap-mib";args[n++]=a->card_swap_arg;args[n++]="--confirm";
    }
    args[n]=NULL;if(!launch_job(a,kind,args))return false;
    a->result_return=CARD_PAGE;return true;
}
static void card_fit_choices(App*a){
    CardDisk*c=card_current(a);if(!c)return;
    if(!card_files_bytes(c,a->card_linux,a->card_swap))a->card_swap=0;
    if(!card_files_bytes(c,a->card_linux,a->card_swap))a->card_linux=0;
}
static void complete_card_job(App*a,const Json*j,int kind,bool ok){
    if(!ok||!j){
        char code[64]="CARD_FAILED",message[256]="The card helper did not return a result.";
        if(j){int error=json_get(j,0,"error");if(error>=0&&j->tokens[error].type==J_OBJECT){jstring(j,error,"code",code,sizeof(code));jstring(j,error,"message",message,sizeof(message));}}
        if(kind==J_CARD_LIST){a->card_known=true;a->card_count=0;a->card_index=-1;copy(a->card_error,sizeof(a->card_error),message);a->dirty=true;return;}
        a->card_known=false;files_problem(a,code,message,CARD_PAGE);return;
    }
    if(kind==J_CARD_LIST){
        char chosen[sizeof(a->cards[0].id)]={0};CardDisk*before=card_current(a);if(before)copy(chosen,sizeof(chosen),before->id);
        a->card_count=0;a->card_index=-1;a->card_error[0]=0;int array=json_get(j,0,"disks");
        for(int i=array+1;array>=0&&i<j->count&&j->tokens[i].start<j->tokens[array].end&&a->card_count<CARD_DISKS;i=json_next(j,i)){
            if(j->tokens[i].type!=J_OBJECT)continue;
            CardDisk*c=&a->cards[a->card_count];memset(c,0,sizeof(*c));
            jstring(j,i,"id",c->id,sizeof(c->id));jstring(j,i,"name",c->name,sizeof(c->name));jstring(j,i,"kind",c->kind,sizeof(c->kind));
            jstring(j,i,"token",c->token,sizeof(c->token));jstring(j,i,"table",c->table,sizeof(c->table));
            jstring(j,i,"busy_code",c->busy_code,sizeof(c->busy_code));jstring(j,i,"busy",c->busy,sizeof(c->busy));
            c->size=json_uint(j,json_get(j,i,"size_bytes"),0);c->usable=json_uint(j,json_get(j,i,"usable_bytes"),0);
            c->align=json_uint(j,json_get(j,i,"align_bytes"),0);c->min_files=json_uint(j,json_get(j,i,"min_files_bytes"),0);
            int parts=json_get(j,i,"partitions");
            for(int p=parts+1;parts>=0&&p<j->count&&j->tokens[p].start<j->tokens[parts].end;p=json_next(j,p)){
                if(j->tokens[p].type!=J_OBJECT)continue;
                if(c->part_count<CARD_PARTS){
                    c->parts[c->part_count].number=(unsigned int)json_uint(j,json_get(j,p,"number"),0);c->parts[c->part_count].size=json_uint(j,json_get(j,p,"size_bytes"),0);
                    jstring(j,p,"fs_type",c->parts[c->part_count].fs,sizeof(c->parts[0].fs));jstring(j,p,"label",c->parts[c->part_count].label,sizeof(c->parts[0].label));
                }
                c->part_count++; /* Counted beyond what is shown, so that the page can say how many more there are. */
            }
            if(c->id[0]&&strlen(c->token)==64&&c->align&&c->min_files)a->card_count++;
        }
        for(unsigned int n=0;n<a->card_count;n++)if(!strcmp(a->cards[n].id,chosen))a->card_index=(int)n;
        if(a->card_index<0&&a->card_count)a->card_index=0;
        card_fit_choices(a);a->card_known=true;a->dirty=true;return;
    }
    /* Partitioned: the result names what the card holds now. */
    char text[384]="",size[48];int made=json_get(j,0,"created");
    for(int p=made+1;made>=0&&p<j->count&&j->tokens[p].start<j->tokens[made].end;p=json_next(j,p)){
        if(j->tokens[p].type!=J_OBJECT)continue;
        char what[16]={0};jstring(j,p,"kind",what,sizeof(what));human_bytes(json_uint(j,json_get(j,p,"size_bytes"),0),size,sizeof(size));
        const char*name=!strcmp(what,"files")?"Files (FAT32)":(!strcmp(what,"linux")?"Linux (ext4)":"Swap");
        if(text[0])append(text,sizeof(text),"\n");
        append(text,sizeof(text),tr(name));append(text,sizeof(text),": ");append(text,sizeof(text),size);
    }
    a->successful=true;copy(a->status,sizeof(a->status),"Card partitioned");
    snprintf(a->message,sizeof(a->message),"%s\n\n%s",text,tr("Mount the card in Files to use it."));
    a->card_known=false;a->result_return=CARD_PAGE;screen(a,RESULT);
}
static void open_card(App*a){
    a->card_known=false;a->card_count=0;a->card_index=-1;a->card_linux=a->card_swap=0;a->card_error[0]=0;a->page_scroll=0;screen(a,CARD_PAGE);
    /* A read that cannot start must not leave the page reading for ever. */
    if(!start_card_job(a,J_CARD_LIST)&&!a->preview){a->card_known=true;copy(a->card_error,sizeof(a->card_error),"The card cannot be read now.");}
}
static void card_chips(App*a,int y,const char*title,const unsigned int*values,unsigned int count,unsigned int chosen,int action,const CardDisk*c,bool linux_row,bool enabled){
    text(a,52,y,0,ORANGE,title);
    int width=(616-(int)(count-1)*8)/(int)count;
    for(unsigned int n=0;n<count;n++){
        Rect r={52+(int)n*(width+8),y+36,width,64};bool on=values[n]==chosen;
        bool fits=c&&card_files_bytes(c,linux_row?values[n]:a->card_linux,linux_row?a->card_swap:values[n])>0;
        char label[24];if(values[n])snprintf(label,sizeof(label),T("%u GiB"),values[n]);else copy(label,sizeof(label),tr("None"));
        rounded(a,r,32,on?ORANGE:PANEL2);raw_text(true);centered(a,r,text_width(label,1)<=r.w-12?1:0,on?ui_theme->on_accent:(fits?TEXT:MUTED),label);raw_text(false);
        register_button(a,r,action+(int)n,enabled&&fits&&!on,0);
    }
}
static void render_card(App*a){
    heading(a,"Partition a card","A new partition table for an SD card or a USB drive.",true);
    bool busy=a->job.pid&&a->job.kind==J_CARD_LIST,free_ui=ui_free(a);CardDisk*c=card_current(a);
    if(!a->card_known||busy||!c){
        rounded(a,(Rect){28,242,664,300},24,PANEL);icon(a,A_STORAGE,315,270,90,ORANGE);
        centered(a,(Rect){48,380,624,50},1,TEXT,!a->card_known||busy?"Reading the card...":"No card found");
        if(a->card_known&&!busy)wrapped(a,62,440,596,0,MUTED,a->card_error[0]?a->card_error:"Insert an SD card or attach a USB drive, then read again.",3);
        button(a,(Rect){28,1340,664,84},"Read again",A_CARD,free_ui&&!busy,false,0);
        return;
    }
    unsigned int shown=c->part_count<CARD_PARTS?c->part_count:CARD_PARTS;int height=150+(shown?(int)shown*38:38)+(c->part_count>shown?38:0);
    rounded(a,(Rect){28,242,664,height},24,PANEL);text(a,52,258,0,ORANGE,"ON THE CARD NOW");
    char size[48],line[160];human_bytes(c->size,size,sizeof(size));
    raw_text(true);ellipsis(a,52,294,436,2,TEXT,c->name[0]?c->name:c->id);raw_text(false);
    raw_text(true);text(a,668-text_width(size,1),300,1,MUTED,size);raw_text(false);
    int y=356;
    if(!shown)text(a,52,y,0,MUTED,"No partitions");
    for(unsigned int n=0;n<shown;n++,y+=38){
        human_bytes(c->parts[n].size,size,sizeof(size));
        snprintf(line,sizeof(line),"%u   %s   %s%s%s",c->parts[n].number,c->parts[n].fs[0]?c->parts[n].fs:"?",size,c->parts[n].label[0]?"   ":"",c->parts[n].label);
        raw_text(true);ellipsis(a,52,y,616,1,TEXT,line);raw_text(false);
    }
    if(c->part_count>shown){snprintf(line,sizeof(line),T("and %u more"),c->part_count-shown);text(a,52,y,0,MUTED,line);}
    y=242+height+16;
    if(a->card_count>1){button(a,(Rect){28,y,664,72},"Next card",A_CARD_NEXT,free_ui,false,0);y+=88;}
    rounded(a,(Rect){28,y,664,330},24,PANEL);
    card_chips(a,y+18,"LINUX (EXT4)",card_linux_gib,CARD_LINUX_CHOICES,a->card_linux,A_CARD_LINUX,c,true,free_ui);
    card_chips(a,y+130,"SWAP",card_swap_gib,CARD_SWAP_CHOICES,a->card_swap,A_CARD_SWAP,c,false,free_ui);
    uint64_t files=card_files_bytes(c,a->card_linux,a->card_swap);human_bytes(files,size,sizeof(size));
    text(a,52,y+248,0,ORANGE,"FILES (FAT32)");
    raw_text(true);text(a,52,y+280,1,files?TEXT:RED,files?size:"-");raw_text(false);
    text(a,668-text_width(tr("the rest of the card"),0),y+286,0,MUTED,"the rest of the card");
    y+=346;
    if(c->busy_code[0])y+=text_tile(a,y,1,ORANGE,c->busy,3)+12;
    text_tile(a,y,0,RED,"Everything on this card is erased: its partitions, systems and files.",3);
    button(a,(Rect){28,1320,664,104},"Hold 5 seconds: erase and partition",A_CARD_APPLY,free_ui&&!c->busy_code[0]&&files>0,true,5000);
}
static bool card_action(App*a,int id){
    if(id==A_CARD&&(a->screen==WIPE||a->screen==CARD_PAGE)){
        if(!ui_free(a))return true;
        if(a->screen==WIPE)open_card(a);else{a->card_known=false;a->card_error[0]=0;start_card_job(a,J_CARD_LIST);}
        return true;
    }
    if(a->screen!=CARD_PAGE)return false;
    if(id==A_BACK){reopen(a,came_from(a,WIPE));return true;}
    CardDisk*c=card_current(a);if(!c||!ui_free(a))return id==A_CARD_NEXT||id==A_CARD_APPLY||(id>=A_CARD_LINUX&&id<A_CARD_SWAP+(int)CARD_SWAP_CHOICES);
    if(id==A_CARD_NEXT){a->card_index=(a->card_index+1)%(int)a->card_count;card_fit_choices(a);a->dirty=true;return true;}
    if(id>=A_CARD_LINUX&&id<A_CARD_LINUX+(int)CARD_LINUX_CHOICES){
        if(card_files_bytes(c,card_linux_gib[id-A_CARD_LINUX],a->card_swap))a->card_linux=card_linux_gib[id-A_CARD_LINUX];
        a->dirty=true;return true;
    }
    if(id>=A_CARD_SWAP&&id<A_CARD_SWAP+(int)CARD_SWAP_CHOICES){
        if(card_files_bytes(c,a->card_linux,card_swap_gib[id-A_CARD_SWAP]))a->card_swap=card_swap_gib[id-A_CARD_SWAP];
        a->dirty=true;return true;
    }
    if(id==A_CARD_APPLY){
        if(!start_card_job(a,J_CARD_APPLY))files_problem(a,"CARD_UNAVAILABLE","The card cannot be partitioned now. Read it again.",CARD_PAGE);
        return true;
    }
    return false;
}
#endif
