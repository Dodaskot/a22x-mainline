/* SPDX-License-Identifier: MIT — the Phone identity page with the real filesystem.py and
   identity_backup.py on a fixture: volume list, partition list, hold to back up (2026-11-08). */
#define main recovery_ui_program_main
#include "recovery_ui.c"
#undef main
#define ICHECK(e) do{checks++;if(!(e)){fprintf(stderr,"identity-ui check %u line %d failed: %s; screen=%d status=%s message=%s\n",checks,__LINE__,#e,a->screen,a->status,a->message);return 1;}}while(0)
static void settle(App*a){uint64_t d=milliseconds()+30000;while(a->job.pid&&milliseconds()<d){a->now=milliseconds();poll_job(a);struct timespec p={0,1000000};nanosleep(&p,NULL);}}
int main(int argc,char**argv){
    if(argc!=4)return 2;
    unsigned checks=0;App*a=calloc(1,sizeof(*a));if(!a)return 2;initialize(a);
    copy(a->fixture,sizeof(a->fixture),argv[1]);copy(a->python,sizeof(a->python),argv[3]);
    snprintf(a->distro_helper,sizeof(a->distro_helper),"%s/distro.py",argv[2]);snprintf(a->filesystem_helper,sizeof(a->filesystem_helper),"%s/filesystem.py",argv[2]);
    screen(a,BACKUP);render(a);ICHECK(test_button(a,A_IDENTITY));
    action(a,A_IDENTITY);ICHECK(a->screen==IDENTITY);
    for(int n=0;n<4;n++)settle(a);
    ICHECK(a->screen==IDENTITY&&a->id_known&&!a->id_error[0]&&a->id_count==3&&identity_volume(a)&&!strcmp(a->id_volume_id,"sdcard-p1"));
    render(a);Button*b=test_button(a,A_ID_BACKUP);ICHECK(b&&b->enabled);
    ICHECK(!test_press(a,A_ID_BACKUP,1500)||!a->job.pid);ICHECK(!a->id_done[0]);
    ICHECK(test_press(a,A_ID_BACKUP,2100));settle(a);
    ICHECK(a->screen==IDENTITY&&!strncmp(a->id_done,"/A22xIdentityBackups/identity-",30));
    char manifest[PATH_LEN*2];snprintf(manifest,sizeof(manifest),"%s/sd%s/manifest.json",argv[1],a->id_done);ICHECK(access(manifest,R_OK)==0);
    /* The partition changes; the copy is written back from the restore page. */
    char part[PATH_LEN*2];snprintf(part,sizeof(part),"%s/parts/efs",argv[1]);FILE*f=fopen(part,"r+b");ICHECK(f);fputs("broken",f);fclose(f);
    ICHECK(test_press(a,A_ID_RESTORE_OPEN,50)&&a->screen==IDENTITY_RESTORE);settle(a);
    ICHECK(a->id_backup_count==1&&a->id_backups[0].same&&a->id_backups[0].parts==3);
    render(a);b=test_button(a,A_ID_RESTORE);ICHECK(b&&!b->enabled);
    ICHECK(test_press(a,A_ID_ITEM+0,50)&&a->id_pick==0);
    ICHECK(test_press(a,A_ID_RESTORE,2500)&&!a->job.pid);
    ICHECK(test_press(a,A_ID_RESTORE,3100));settle(a);
    ICHECK(a->screen==IDENTITY_RESTORE&&a->successful);
    char saved[PATH_LEN*3];snprintf(saved,sizeof(saved),"%s/sd%s/efs.img",argv[1],a->id_done);
    FILE*x=fopen(part,"rb"),*y=fopen(saved,"rb");ICHECK(x&&y);int same=1,c1,c2;while((c1=fgetc(x))!=EOF){c2=fgetc(y);if(c1!=c2){same=0;break;}}fclose(x);fclose(y);ICHECK(same);
    action(a,A_BACK);ICHECK(a->screen==IDENTITY);
    /* 2026-11-10: the Android partitions of the same page, one of them left out. */
    render(a);ICHECK(test_press(a,A_ID_KIND+1,50)&&a->id_kind==1);
    for(int n=0;n<4;n++)settle(a);
    ICHECK(a->screen==IDENTITY&&a->id_known&&!a->id_error[0]&&a->id_count==3&&!strcmp(a->id_labels[1],"vbmeta")&&!strcmp(a->id_volume_id,"sdcard-p1"));
    render(a);ICHECK(test_press(a,A_ID_PART+1,50)&&identity_chosen(a,NULL)==2);
    /* userdata is skipped when choosing the next volume: Android's factory reset erases it. */
    render(a);ICHECK(test_press(a,A_ID_DEST_NEXT,50)&&!strcmp(a->id_volume_id,"sdcard-p1"));
    render(a);ICHECK(test_press(a,A_ID_BACKUP,2100));settle(a);
    /* 2026-11-11: a copy of Android is one compressed archive, and nothing half-written is left beside it. */
    size_t done=strlen(a->id_done);
    ICHECK(a->screen==IDENTITY&&!strncmp(a->id_done,"/A22xAndroidBackups/android-",28)&&done>8&&!strcmp(a->id_done+done-8,".tar.zst"));
    char folder[PATH_LEN*2];snprintf(folder,sizeof(folder),"%s/sd%s",argv[1],a->id_done);
    struct stat packed;ICHECK(stat(folder,&packed)==0&&S_ISREG(packed.st_mode)&&packed.st_size>0&&packed.st_size<(1<<20));
    /* Its copies are restored from the Restore page, in Android's category: the page of the copy
       names its two partitions, checks it, restores it (nothing differs) and deletes it by a hold. */
    ICHECK(test_press(a,A_ID_RESTORE_OPEN,50)&&a->screen==LIST&&list_android(a));for(int n=0;n<3;n++)settle(a);
    ICHECK(a->copies_known&&a->copy_count==1&&a->copies[0].archive&&a->copies[0].same&&a->copies[0].writable&&a->copies[0].parts==2);
    ICHECK(!strcmp(a->copies[0].labels[0],"boot")&&!strcmp(a->copies[0].labels[1],"super")&&!strcmp(a->copies[0].path,a->id_done)&&a->copies[0].file_bytes==(uint64_t)packed.st_size);
    ICHECK(test_press(a,A_COPY_ITEM,50)&&a->screen==COPY_DETAIL);
    ICHECK(test_press(a,A_COPY_VERIFY,50));settle(a);
    ICHECK(a->screen==RESULT&&a->successful&&a->result_return==COPY_DETAIL&&test_press(a,A_DONE,50)&&a->screen==COPY_DETAIL);
    ICHECK(test_press(a,A_COPY_RESTORE,3100));settle(a);
    ICHECK(a->screen==RESULT&&a->successful&&strstr(a->message,"nothing was written")&&test_press(a,A_DONE,50)&&a->screen==COPY_DETAIL);
    render(a);b=test_button(a,A_COPY_DELETE);ICHECK(b&&b->enabled&&b->hold_ms==3000);
    ICHECK(test_press(a,A_COPY_DELETE,2500)&&!a->job.pid&&access(folder,R_OK)==0);
    ICHECK(test_press(a,A_COPY_DELETE,3100));for(int n=0;n<3;n++)settle(a);
    ICHECK(a->screen==LIST&&a->successful&&a->copies_known&&a->copy_count==0&&a->copy_pick==-1&&access(folder,F_OK)!=0);
    ICHECK(access(manifest,R_OK)==0); /* The identity copy made first is another kind and stays. */
    action(a,A_BACK);ICHECK(a->screen==IDENTITY);
    action(a,A_BACK);ICHECK(a->screen==BACKUP);
    printf("{\"ok\":true,\"checks\":%u}\n",checks);free(a->pixels);free(a);return 0;
}
