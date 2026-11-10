/* SPDX-License-Identifier: MIT — native render examples, never device state. */
#define main recovery_ui_program_main
#include "recovery_ui.c"
#undef main
int main(int argc,char**argv){
    if(argc!=3)return 2;
    App*a=calloc(1,sizeof(*a));if(!a)return 2;initialize(a);a->preview=true;a->render_native=true;
    copy(a->battery,sizeof(a->battery),"80%");copy(a->kernel,sizeof(a->kernel),"Linux / illustrative native render");
    const char*which=argv[1];InstallState*m=&a->install;
    m->known=m->flash_boot=m->stock=m->stock_profile=m->stock_qualified=m->can_resume=true;
    m->stock_selected=strcmp(which,"install-boot-plan")!=0;
    copy(m->source_volume,sizeof(m->source_volume),"sdcard-p1");copy(m->source_token,sizeof(m->source_token),"preview");
    copy(m->source_path,sizeof(m->source_path),m->stock_selected?"/Файли відновлення/Stock A226B DZC3/manifest.json":"/Образи Linux/a22x-k107-linux.img");
    copy(m->backup_path,sizeof(m->backup_path),"/mnt/backup/A22xBackups/linux-verified-snapshot");
    copy(m->profile_id,sizeof(m->profile_id),"A226B / illustrative fixed stock profile");copy(m->firmware,sizeof(m->firmware),"A226BXXSBDZC3");
    copy(m->operation,sizeof(m->operation),m->stock_selected?"install-stock":"flash-boot");
    copy(m->checkpoint,sizeof(m->checkpoint),"/mnt/backup/.a22x-installs/0123456789abcdef0123456789abcdef/platform");
    copy(m->result_checkpoint,sizeof(m->result_checkpoint),m->checkpoint);copy(m->result_journal,sizeof(m->result_journal),"/mnt/backup/.a22x-installs/0123456789abcdef0123456789abcdef/journal.json");
    copy(m->checkpoint_labels,sizeof(m->checkpoint_labels),"boot, recovery, dtbo, vbmeta, vbmeta_system");m->checkpoint_bytes=84017152;
    copy(m->transaction,sizeof(m->transaction),"0123456789abcdef0123456789abcdef");
    m->backup_complete=m->backup_verified=m->backup_same_device=m->backup_current=m->backup_userdata_current=true;m->backup_current_required=m->backup_userdata_required=m->stock_selected;
    m->plan_valid=m->can_execute=true;m->hold_ms=m->stock_selected?5000:3000;m->expires=(uint64_t)time(NULL)+900;
    const struct {const char*label;unsigned partition;uint64_t start,bytes;bool skip;} targets[]={
        {"super",48,1605632,7239368704ULL,false},{"prism",49,15745024,738197504,false},
        {"optics",50,17186816,25165824,false},{"dtbo",20,423936,8388608,true},
        {"vbmeta_system",43,1582080,65536,true},{"vbmeta",21,440320,65536,true},
        {"boot",41,1434624,33554432,false},{"recovery",42,1500160,41943040,false}};
    for(unsigned n=0;n<sizeof(targets)/sizeof(targets[0]);n++){
        if(!m->stock_selected&&strcmp(targets[n].label,"boot"))continue;
        InstallTarget*t=targets[n].skip?&m->skips[m->skip_count++]:&m->writes[m->write_count++];
        copy(t->label,sizeof(t->label),targets[n].label);snprintf(t->path,sizeof(t->path),"/dev/mmcblk0p%u",targets[n].partition);copy(t->action,sizeof(t->action),targets[n].skip?"skip-identical":"write-raw");t->partition=targets[n].partition;t->start=targets[n].start;t->bytes=targets[n].bytes;
        if(!targets[n].skip)copy(m->order[m->order_count++],sizeof(m->order[0]),targets[n].label);
    }
    m->recovery_written=m->stock_selected;
    if(!strcmp(which,"tools-more")){screen(a,TOOLS);a->tools_scroll=tools_max_scroll(a);}
    else if(!strcmp(which,"install"))screen(a,INSTALL);
    else if(!strcmp(which,"install-pending")){
        m->pending_count=3;m->reboot_blocked=true;
        for(unsigned n=0;n<m->pending_count;n++){InstallPending*p=&m->pending[n];snprintf(p->id,sizeof(p->id),"%032u",n+1);copy(p->operation,sizeof(p->operation),n?"flash-boot":"install-stock");copy(p->state,sizeof(p->state),n==2?"Manual inspection required":"Writing was interrupted");p->resumable=n!=2;}
        screen(a,INSTALL);
    }else if(!strcmp(which,"install-source")){
        m->pick_source=true;a->volume_count=1;a->volume_index=0;a->volumes[0].mounted=a->volumes[0].write_supported=true;copy(a->volumes[0].id,sizeof(a->volumes[0].id),"sdcard-p1");copy(a->volumes[0].fs_type,sizeof(a->volumes[0].fs_type),"ext4");copy(a->volumes[0].token,sizeof(a->volumes[0].token),"preview");
        copy(a->fs_selected.name,sizeof(a->fs_selected.name),"manifest.json");copy(a->fs_selected.path,sizeof(a->fs_selected.path),m->source_path);copy(a->fs_selected.type,sizeof(a->fs_selected.type),"file");copy(a->fs_selected.token,sizeof(a->fs_selected.token),"preview");screen(a,FILE_INFO);
    }else{
        bool result=!strcmp(which,"install-result");
        if(!strcmp(which,"install-blocked")){m->can_execute=false;copy(m->blockers,sizeof(m->blockers),"STOCK_PROFILE_NOT_QUALIFIED");}
        if(!install_build_review(a,result))return 3;
        screen(a,result?INSTALL_RESULT:INSTALL_PLAN);
        if(!strcmp(which,"install-targets"))m->review_scroll=500;
        else if(strcmp(which,"install-boot-plan")&&strcmp(which,"install-stock-plan"))m->review_scroll=install_review_max_scroll(a);
    }
    render(a);int rc=write_ppm(a,argv[2])?0:1;free(a->pixels);free(a);return rc;
}
