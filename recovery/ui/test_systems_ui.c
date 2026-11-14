/* SPDX-License-Identifier: MIT — actual UI + actual directory-only system backend. */
#define main recovery_ui_program_main
#include "recovery_ui.c"
#undef main
#define SCHECK(expr) do{checks++;if(!(expr)){fprintf(stderr,"systems-ui check %u line %d failed: %s; screen=%d status=%s message=%s\n",checks,__LINE__,#expr,a->screen,a->status,a->message);free(a->pixels);free(a);return 1;}}while(0)
static bool systems_settle(App*a){uint64_t deadline=milliseconds()+30000;while(a->job.pid&&milliseconds()<deadline){a->now=milliseconds();poll_job(a);struct timespec pause={0,1000000};nanosleep(&pause,NULL);}return !a->job.pid;}
int main(int argc,char**argv){
    if(argc!=5)return 2;
    unsigned checks=0;const char*which=argv[3];App*a=calloc(1,sizeof(*a));if(!a)return 2;initialize(a);
    copy(a->fixture,sizeof(a->fixture),argv[1]);copy(a->distro_helper,sizeof(a->distro_helper),argv[2]);copy(a->python,sizeof(a->python),argv[4]);
    char*slash=strrchr(a->distro_helper,'/');if(!slash)return 2;size_t root_length=(size_t)(slash-a->distro_helper);
    if(root_length+15>=sizeof(a->filesystem_helper))return 2;
    memcpy(a->filesystem_helper,a->distro_helper,root_length);copy(a->filesystem_helper+root_length,sizeof(a->filesystem_helper)-root_length,"/filesystem.py");
    SCHECK(snprintf(a->workdir,sizeof(a->workdir),"%s/privateplans",argv[1])<(int)sizeof(a->workdir)&&private_workdir(a->workdir));
    a->fs_dirty=true;copy(a->fs_text,sizeof(a->fs_text),"unsaved system editor ґ");screen(a,OS_MENU);
    SCHECK(test_press(a,A_SYSTEMS,50)&&a->screen==RESULT&&a->result_return==FILE_EDIT&&!a->job.pid&&!strcmp(a->fs_text,"unsaved system editor ґ"));
    a->fs_dirty=false;screen(a,OS_MENU);SCHECK(test_press(a,A_SYSTEMS,50)&&systems_settle(a)&&a->screen==SYSTEMS&&a->systems_known);
    if(!strcmp(which,"android-plan")){
        /* 2026-11-14, on the phone: a check that found the storage busy is asked again a few seconds
           later, not given up until the next start. */
        {static const char busy[]="{\"ok\":false,\"operation\":\"show\",\"error\":{\"code\":\"OPERATION_BUSY\",\"message\":\"busy\"}}";
         JsonToken t[16];Json bj;SCHECK(json_parse(&bj,busy,t,16)>0);a->now=1000;a->aplan_asked=true;
         complete_aplan_job(a,&bj,J_APLAN_SHOW,false);SCHECK(!a->aplan_asked&&!a->aplan_known&&a->aplan_tries==1&&a->aplan_retry_at==1000+APLAN_RETRY_MS);
         screen(a,HOME);android_plan_tick(a);SCHECK(!a->job.pid&&!a->aplan_asked);a->now=1000+APLAN_RETRY_MS;}
        /* 2026-11-10: the plan the Android app left in cache opens by itself over the home page. */
        screen(a,HOME);android_plan_tick(a);SCHECK(a->job.kind==J_APLAN_SHOW&&systems_settle(a));
        SCHECK(a->screen==APLAN&&a->aplan_valid&&!strcmp(a->aplan_catalog,"ubuntu-24.04")&&!a->aplan_erase&&a->aplan_script_present&&!strcmp(a->aplan_user,"olena"));
        render(a);Button*review=test_button(a,A_APLAN_REVIEW);SCHECK(review&&review->enabled);
        /* The Install page shows it first; a second tick does not ask again. */
        screen(a,OS_MENU);render(a);SCHECK(test_button(a,A_APLAN)!=NULL);android_plan_tick(a);SCHECK(!a->job.pid);
        SCHECK(test_press(a,A_APLAN,50)&&a->screen==APLAN);
        /* Review: cache is mounted for the script, then the usual review of the installation. */
        SCHECK(test_press(a,A_APLAN_REVIEW,50));for(int n=0;n<6&&a->job.pid;n++)systems_settle(a);
        if(a->screen!=SYSTEMS_PLAN)fprintf(stderr,"android plan: screen=%d error=%s\n",a->screen,a->aplan_error);
        SCHECK(a->screen==SYSTEMS_PLAN&&a->systems_plan_valid&&a->systems_plan_kind==J_SYS_IMPORT_PLAN&&!strcmp(a->systems_remote_id,"ubuntu-24.04")&&!a->systems_wipe);
        SCHECK(a->systems_desktop==2&&((a->systems_shells>>3)&1u)&&a->systems_new_account&&!strcmp(a->systems_username,"olena")&&a->systems_password_hash[0]=='$'&&!a->systems_password[0]);
        SCHECK(((a->systems_apps>>8)&1u)&&((a->systems_apps>>15)&1u)&&a->systems_locale==1&&a->systems_tz>=0&&!strcmp(tz_zones[a->systems_tz].id,"Europe/Kyiv"));
        SCHECK(!strcmp(a->systems_script_volume,"cache")&&!strcmp(a->systems_script_path,"/a22x-recovery/scripts/setup.sh")&&a->aplan_used);
        /* A new choice on the pages drops the plan's password hash. */
        systems_forget_plan(a);systems_forget_choices(a);SCHECK(!a->systems_password_hash[0]&&!a->aplan_used);
        /* Discarding deletes the plan from cache, only after the hold. */
        screen(a,APLAN);SCHECK(test_press(a,A_APLAN_FORGET,1000)&&!a->job.pid&&a->aplan_present);
        SCHECK(test_press(a,A_APLAN_FORGET,2100)&&systems_settle(a)&&a->screen==HOME&&!a->aplan_present);
        goto finished;
    }
    if(!strcmp(which,"unmounted")||!strcmp(which,"readonly")||!strcmp(which,"no-profile")){
        SCHECK(!systems_ready(a)&&!test_press(a,A_SYS_IMPORT,50)&&!a->job.pid);
        if(!strcmp(which,"unmounted")){
            /* 2026-11-08, found by tapping on the phone: mounting from New system must stay there. */
            screen(a,SYSTEMS_NEW);SCHECK(test_press(a,A_SYS_MOUNT,50)&&a->systems_mount_sheet);
            SCHECK(test_press(a,A_SYS_MOUNT_RW,50)&&systems_settle(a)&&systems_settle(a));
            for(int n=0;n<5&&a->job.pid;n++)systems_settle(a);
            SCHECK(a->screen==SYSTEMS_NEW&&a->systems_known&&a->systems_mounted&&!a->systems_read_only);
        }
        goto finished;
    }
    if(!strcmp(which,"resume")||!strcmp(which,"rollback")){
        SCHECK(a->systems_transaction[0]&&a->systems_reboot_blocked&&a->systems_step<=a->systems_total);
        a->allow_power=true;screen(a,POWER);SCHECK(!test_press(a,A_REBOOT,50));action(a,A_REBOOT);SCHECK(a->screen==POWER&&a->running);
        a->power_intent=200;screen(a,POWER_CONFIRM);SCHECK(!test_press(a,A_POWER_CONFIRM,2100));action(a,A_POWER_CONFIRM);SCHECK(a->running&&a->exit_code==0);
        screen(a,SYSTEMS);SCHECK(test_press(a,!strcmp(which,"rollback")?A_SYS_ROLLBACK:A_SYS_RESUME,50)&&a->job.kind==J_SYS_STATUS&&systems_settle(a)&&a->screen==SYSTEMS_RECOVER);
        SCHECK(!strcmp(a->systems_expected_transaction,a->systems_transaction));
        SCHECK(test_press(a,A_SYS_RECOVER_CONFIRM,100)&&!a->job.pid&&a->screen==SYSTEMS_RECOVER);
        SCHECK(test_press(a,A_SYS_RECOVER_CONFIRM,3100)&&systems_settle(a)&&a->screen==SYSTEMS&&!a->systems_transaction[0]&&!a->systems_reboot_blocked);
        SCHECK(!strcmp(a->systems_active,!strcmp(which,"rollback")?"original":"new-linux"));goto finished;
    }
    SCHECK(systems_ready(a)&&!a->systems_initialized);
    /* 2026-11-09: Import opens the adding page (catalog or file); nothing is erased from there. */
    SCHECK(test_press(a,A_SYS_IMPORT,50)&&a->screen==SYSTEMS_NEW&&a->systems_adding&&!a->systems_wipe);
    /* 2026-11-11: the sheet over the page asks where the image is; a Linux image comes from a card,
       so userdata is not offered there. */
    SCHECK(test_press(a,A_NEW_FILE,50)&&systems_settle(a)&&a->screen==SYSTEMS_NEW&&a->source_sheet&&a->source_known&&a->systems_pick_manifest&&!a->systems_wipe);
    int sd=-1,user=-1;for(int n=0;n<a->volume_count;n++){if(!strcmp(a->volumes[n].id,"sdcard-p1"))sd=n;if(!strcmp(a->volumes[n].id,"userdata"))user=n;}
    SCHECK(sd>=0&&user>=0&&!test_press(a,A_SOURCE_ITEM+user,50)&&!a->job.pid);
    SCHECK(test_press(a,A_SOURCE_ITEM+sd,50)&&systems_settle(a)&&a->screen==FILE_BROWSER&&!a->source_sheet);
    int manifest=-1,archive=-1,odd=-1,sum=-1;
    for(int n=0;n<a->file_count;n++){
        const char*name=a->files[n].name;
        if(!strcmp(name,"bundle.json"))manifest=n;
        if(!strcmp(name,"rootfs.tar.gz"))archive=n;
        if(!strcmp(name,"Fedora-Cloud.qcow2.man"))odd=n;
        if(!strcmp(name,"rootfs.tar.gz.sha256"))sum=n;
    }
    SCHECK(manifest>=0&&archive>=0&&odd>=0&&sum>=0);
    /* An image with a suffix left by the download is offered; a small checksum file is not. */
    SCHECK(test_press(a,A_FS_ENTRY+odd,50)&&systems_settle(a)&&a->screen==FILE_INFO&&systems_manifest_selected(a));
    SCHECK(test_press(a,A_BACK,50)&&systems_settle(a)&&a->screen==FILE_BROWSER);
    SCHECK(test_press(a,A_FS_ENTRY+sum,50)&&systems_settle(a)&&a->screen==FILE_INFO&&!systems_manifest_selected(a));
    SCHECK(test_press(a,A_BACK,50)&&systems_settle(a)&&a->screen==FILE_BROWSER);
    SCHECK(test_press(a,A_FS_ENTRY+archive,50)&&systems_settle(a)&&a->screen==FILE_INFO&&systems_manifest_selected(a));
    SCHECK(test_press(a,A_BACK,50)&&systems_settle(a)&&a->screen==FILE_BROWSER);
    SCHECK(test_press(a,A_FS_ENTRY+manifest,50)&&systems_settle(a)&&a->screen==FILE_INFO&&systems_manifest_selected(a));
    SCHECK(!test_press(a,A_FS_DELETE,3100)&&!test_press(a,A_FS_EDIT,50));
    SCHECK(test_press(a,A_SYS_USE_MANIFEST,50)&&systems_settle(a)&&a->screen==SYSTEMS_PLAN&&a->systems_plan_valid&&a->systems_plan_kind==J_SYS_IMPORT_PLAN);
    SCHECK(test_press(a,A_SYS_PLAN_CONFIRM,100)&&!a->job.pid&&a->systems_plan_valid);
    SCHECK(test_press(a,A_SYS_PLAN_CONFIRM,2100)&&systems_settle(a));
    if(!strcmp(which,"accounts")){SCHECK(a->screen==RESULT&&!strcmp(a->status,"HOME_ACCOUNT_MISMATCH")&&!a->systems_plan_valid);goto finished;}
    /* 2026-11-08: the receipt opens the completion page; a system saved beside opens Systems from there. */
    SCHECK(a->screen==SYSTEMS_DONE&&!a->systems_done_clean&&test_press(a,A_SYS_DONE_SYSTEMS,50)&&systems_settle(a));
    SCHECK(a->screen==SYSTEMS&&a->systems_slot_count==2&&!strcmp(a->systems_active,"original")&&!a->systems_reboot_blocked);
    int target=-1;for(int n=0;n<a->systems_slot_count;n++)if(!strcmp(a->systems_slots[n].id,"new-linux"))target=n;SCHECK(target>=0);
    if(!strcmp(which,"remove-refused")){
        /* 2026-11-10, on the phone: a switch refused for another platform profile still offers
           removal, and the removal page names the tapped system, not a name left from a plan. */
        a->systems_selected=target;copy(a->systems_plan_slot,sizeof(a->systems_plan_slot),a->systems_slots[target].id);
        copy(a->systems_plan_name,sizeof(a->systems_plan_name),"name left from an earlier plan");
        static const char reply[]="{\"ok\":false,\"operation\":\"switch-plan\",\"error\":{\"code\":\"PLATFORM_PROFILE_MISMATCH\",\"message\":\"x\",\"details\":{}}}";
        JsonToken tokens[32];Json doc;SCHECK(json_parse(&doc,reply,tokens,32)>0);
        complete_systems_job(a,&doc,J_SYS_SWITCH_PLAN,false);
        SCHECK(a->screen==RESULT&&a->systems_remove_offer&&!strcmp(a->status,"PLATFORM_PROFILE_MISMATCH"));
        SCHECK(test_press(a,A_SYS_REMOVE,50)&&a->screen==SYSTEMS_REMOVE&&!a->systems_remove_offer&&!strcmp(a->systems_plan_name,a->systems_slots[target].name));
        SCHECK(test_press(a,A_SYS_REMOVE_CONFIRM,3100)&&systems_settle(a)&&a->screen==SYSTEMS&&a->systems_known&&a->systems_slot_count==1&&!strcmp(a->systems_active,"original"));
        /* A refusal for the running system offers nothing to remove. */
        complete_systems_job(a,&doc,J_SYS_SWITCH_PLAN,false);a->systems_plan_slot[0]=0;
        static const char active[]="{\"ok\":false,\"operation\":\"switch-plan\",\"error\":{\"code\":\"ALREADY_ACTIVE\",\"message\":\"x\",\"details\":{}}}";
        SCHECK(json_parse(&doc,active,tokens,32)>0);copy(a->systems_plan_slot,sizeof(a->systems_plan_slot),"original");
        complete_systems_job(a,&doc,J_SYS_SWITCH_PLAN,false);SCHECK(a->screen==RESULT&&!a->systems_remove_offer);
        SCHECK(test_press(a,A_DONE,50)&&a->screen==SYSTEMS);goto finished;
    }
    SCHECK(test_press(a,A_SYS_SLOT+target,50)&&systems_settle(a));
    SCHECK(a->screen==SYSTEMS_PLAN&&a->systems_plan_valid&&a->systems_plan_kind==J_SYS_SWITCH_PLAN);
    if(!strcmp(which,"tamper")){
        int fd=open(a->systems_plan_path,O_WRONLY|O_APPEND|O_NOFOLLOW|O_CLOEXEC);SCHECK(fd>=0);ssize_t written=write(fd," ",1);int rc=close(fd);SCHECK(written==1&&rc==0);
        SCHECK(test_press(a,A_SYS_PLAN_CONFIRM,3100)&&systems_settle(a)&&a->screen==RESULT&&!strcmp(a->status,"PLAN_FILE_CHANGED"));
        SCHECK(test_press(a,A_DONE,50)&&systems_settle(a)&&a->screen==SYSTEMS&&!strcmp(a->systems_active,"original")&&!a->systems_reboot_blocked);goto finished;
    }
    SCHECK(test_press(a,A_SYS_PLAN_CONFIRM,2000)&&!a->job.pid&&a->systems_plan_valid);
    SCHECK(test_press(a,A_SYS_PLAN_CONFIRM,3100)&&systems_settle(a)&&a->screen==SYSTEMS&&!strcmp(a->systems_active,"new-linux")&&!a->systems_reboot_blocked);
    int original=-1;for(int n=0;n<a->systems_slot_count;n++)if(!strcmp(a->systems_slots[n].id,"original"))original=n;SCHECK(original>=0);
    SCHECK(test_press(a,A_SYS_SLOT+original,50)&&systems_settle(a)&&a->screen==SYSTEMS_PLAN&&a->systems_plan_valid);
    SCHECK(test_press(a,A_SYS_PLAN_CONFIRM,3100)&&systems_settle(a)&&a->screen==SYSTEMS&&!strcmp(a->systems_active,"original"));
    /* 2026-11-09: a saved system that is not running is removed from its review (backend remove-slot). */
    target=-1;for(int n=0;n<a->systems_slot_count;n++)if(!strcmp(a->systems_slots[n].id,"new-linux"))target=n;SCHECK(target>=0);
    SCHECK(test_press(a,A_SYS_SLOT+target,50)&&systems_settle(a)&&a->screen==SYSTEMS_PLAN&&a->systems_plan_kind==J_SYS_SWITCH_PLAN);
    SCHECK(test_press(a,A_SYS_REMOVE,50)&&a->screen==SYSTEMS_REMOVE&&!a->systems_plan_valid&&!strcmp(a->systems_plan_slot,"new-linux"));
    SCHECK(test_press(a,A_SYS_REMOVE_CONFIRM,2000)&&!a->job.pid&&a->screen==SYSTEMS_REMOVE);
    SCHECK(test_press(a,A_SYS_REMOVE_CONFIRM,3100)&&systems_settle(a)&&systems_settle(a)&&a->screen==SYSTEMS&&strstr(a->systems_note,"removed"));
    SCHECK(a->systems_known&&a->systems_slot_count==1&&!strcmp(a->systems_active,"original"));
    /* 2026-11-10: an image saved on the phone survived both switches, installs without an SD card
       (userdata is its source) and is deleted only after a hold. */
    screen(a,SYSTEMS_NEW);a->systems_adding=true;
    SCHECK(test_press(a,A_NEW_SAVED,50)&&a->screen==SYSTEMS_IMAGES&&systems_settle(a)&&a->saved_known&&a->saved_count==1&&!strcmp(a->saved[0].file,"rootfs.tar.gz"));
    SCHECK(test_press(a,A_IMAGE_ITEM,50)&&a->screen==SYSTEMS_SETUP&&!strcmp(a->systems_source_volume,"userdata")&&!strcmp(a->systems_manifest,"/.a22x-images/rootfs.tar.gz")&&!a->systems_wipe);
    screen(a,SYSTEMS_IMAGES);
    SCHECK(test_press(a,A_IMAGE_DELETE,50)&&a->saved_delete==1);
    SCHECK(test_press(a,A_IMAGE_DELETE_CONFIRM,1000)&&!a->job.pid&&a->saved_delete==1);
    SCHECK(test_press(a,A_IMAGE_DELETE_CONFIRM,2100)&&systems_settle(a)&&a->saved_count==0&&!a->saved_delete&&strstr(a->saved_note,"deleted"));
    screen(a,SYSTEMS);
    /* Existing slots are pixel-scrollable, with bounded inertia and stop-tap suppression. */
    a->preview=true;a->systems_slot_count=16;for(int n=0;n<16;n++){snprintf(a->systems_slots[n].id,sizeof(a->systems_slots[n].id),"slot-%02d",n);copy(a->systems_slots[n].name,sizeof(a->systems_slots[n].name),"A long prepared ARM64 system");a->systems_slots[n].active=n==0;}
    uint64_t at=milliseconds();workflow_swipe(a,250,960,460,at);SCHECK(a->systems_scroll==500&&a->kinetic.active&&a->motion_surface==SCROLL_SYSTEMS&&!a->job.pid);
    tick_inertia(a,at+120);SCHECK(a->systems_scroll>500&&a->systems_scroll<=systems_max_scroll(a));int64_t offset=a->systems_scroll;render(a);pointer(a,true,250,650,at+130);pointer(a,false,250,650,at+160);SCHECK(!a->kinetic.active&&a->systems_scroll==offset&&!a->job.pid&&a->screen==SYSTEMS);
    systems_scroll_to(a,INT64_MAX);SCHECK(a->systems_scroll==systems_max_scroll(a));systems_scroll_to(a,-1);SCHECK(a->systems_scroll==0);
finished:
    SCHECK(!strcmp(a->fs_text,"unsaved system editor ґ")&&!a->fs_dirty);
    systems_forget_plan(a);free(a->pixels);free(a);printf("{\"ok\":true,\"case\":\"%s\",\"checks\":%u,\"actual_backend\":true,\"directory_fixture\":true,\"device_io\":false}\n",which,checks);return 0;
}
