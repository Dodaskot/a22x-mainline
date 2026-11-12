/* SPDX-License-Identifier: MIT — where Back leads (2026-11-10, found on the phone by its owner:
   in Android > Stock, Back left for the main menu instead of the page before).

   Walk: every page reachable through its enabled buttons is opened, and Back from it must return
   to the page it was opened from. A prompt, a confirmation or a result is never returned to.
   Jobs: the same with a stand-in "python" whose replies take time, as on the phone: Back pressed
   while a page is still read is kept and made after the read; a read that ends for a page already
   left opens nothing; a review returns to the folder its file was chosen in.

   Numbers in a failure are the values of enum Screen and of the actions in recovery_ui.c. */
#define main recovery_ui_program_main
#include "recovery_ui.c"
#undef main

typedef struct{App app;unsigned int language;const Theme*theme;const TerminalScheme*terminal;int depth;char path[384];}State;
static State*queue[4096];static int head,tail;
static bool seen[80][80];
static void save(State*s,const App*a){memcpy(&s->app,a,sizeof(App));s->language=ui_language;s->theme=ui_theme;s->terminal=ui_terminal;}
static void load(const State*s,App*a){memcpy(a,&s->app,sizeof(App));ui_language=s->language;ui_theme=s->theme;ui_terminal=s->terminal;}

/* The pages filled with what a phone shows, then the walk from Home. */
static int walk(void){
    App*a=calloc(1,sizeof(*a));if(!a)return 2;initialize(a);
    static const char*const fill[]={"files","flash-list","systems","android-catalog-all","root-magisk","saved-images","wifi-connected","identity-saved","restore","home-plan","tools","display"};
    for(size_t n=0;n<sizeof(fill)/sizeof(*fill);n++)if(!preview(a,fill[n])){fprintf(stderr,"no preview %s\n",fill[n]);return 2;}
    a->flash_pick=a->samsung_pick=a->stock_boot_pick=false;a->theme_pick=HOME;a->install.pick_source=a->install.pick_backup=false;a->systems_pick_manifest=a->systems_pick_script=false;
    a->fs_prompt_kind=0;a->catalog_return=HOME;a->catalog_saving=false;a->focus=-1;a->button_count=0;
    a->device=true;a->preview=true;a->inventory_ok=true;a->successful=true;a->now=milliseconds();screen(a,HOME);
    State*first=calloc(1,sizeof(State));if(!first)return 2;save(first,a);snprintf(first->path,sizeof(first->path),"0");queue[tail++]=first;
    int wrong=0,edges=0,pages=0;bool page_seen[80]={false};
    while(head<tail){
        State*s=queue[head++];load(s,a);render(a);
        int ids[MAX_BUTTONS],count=0;
        for(int n=0;n<a->button_count&&count<MAX_BUTTONS;n++){
            if(!a->buttons[n].enabled)continue;
            int id=a->buttons[n].action;bool repeated=false;for(int k=0;k<count;k++)if(ids[k]==id)repeated=true;
            if(!repeated)ids[count++]=id;
        }
        for(int k=0;k<count;k++){
            int id=ids[k];
            /* Going back and home are what is tested; a language changes every text, not the page. */
            if(id==A_BACK||id==A_NAV_BACK||id==A_NAV_HOME||id==A_DONE||(id>=A_LANGUAGE_ITEM&&id<A_LANGUAGE_ITEM+LANGUAGE_COUNT))continue;
            load(s,a);render(a);int before=(int)a->screen;tap_action(a,id);int after=(int)a->screen;
            if(after==before)continue;
            edges++;if(after<80&&!page_seen[after]){page_seen[after]=true;pages++;}
            render(a);int back=nav_back_action(a),target=-1;
            State*next=calloc(1,sizeof(State));if(!next)return 2;save(next,a);next->depth=s->depth+1;snprintf(next->path,sizeof(next->path),"%s >%d> %d",s->path,id,after);
            if(back){tap_action(a,back);target=(int)a->screen;}
            if(back==A_BACK&&target!=before&&!nav_passing((enum Screen)before)){
                wrong++;fprintf(stderr,"Back from page %d (opened from %d by action %d) leads to %d; path %s\n",after,before,id,target,s->path);
            }
            if(before<80&&after<80&&!seen[before][after]&&next->depth<7&&tail<4096){seen[before][after]=true;queue[tail++]=next;}else free(next);
        }
        free(s);
    }
    /* The terminal opens over any page (swipe up and hold): Back returns to that page, Home goes home. */
    screen(a,HOME);screen(a,OS_MENU);render(a);tap_action(a,A_NAV_TERMINAL);
    if(a->screen!=TERMINAL){wrong++;fprintf(stderr,"the terminal did not open over page %d\n",(int)OS_MENU);}
    render(a);tap_action(a,A_NAV_BACK);
    if(a->screen!=OS_MENU){wrong++;fprintf(stderr,"Back from the terminal leads to %d, not to page %d under it\n",(int)a->screen,(int)OS_MENU);}
    render(a);tap_action(a,A_NAV_TERMINAL);render(a);tap_action(a,A_NAV_HOME);
    if(a->screen!=HOME){wrong++;fprintf(stderr,"Home from the terminal leads to %d\n",(int)a->screen);}
    /* A partition inside super is picked on its own page: leaving the picker returns there. */
    screen(a,HOME);screen(a,OS_MENU);screen(a,FLASH);screen(a,FLASH_SUPER);a->flash_pick=true;a->flash_restore=false;screen(a,FILE_VOLUMES);render(a);tap_action(a,A_NAV_BACK);
    if(a->screen!=FLASH_SUPER||a->flash_pick){wrong++;fprintf(stderr,"Back from the picker of a partition in super leads to %d\n",(int)a->screen);}
    /* APatch's SuperKey (2026-11-11) is typed on a prompt that returns to Root: a key that does not
       fit stays on the prompt, Cancel keeps the key entered before, and leaving Root forgets it. */
    preview(a,"root-apatch");a->preview=true;screen(a,HOME);screen(a,OS_MENU);screen(a,ANDROID_MENU);screen(a,ROOT_PAGE);
    render(a);tap_action(a,A_ROOT_KEY+1);
    if(a->screen!=FILE_PROMPT||a->fs_prompt_kind!=J_ROOT_SUPERKEY||strcmp(a->fs_prompt,"Preview-Key-2026")){wrong++;fprintf(stderr,"the SuperKey prompt did not open with the key entered before\n");}
    copy(a->fs_prompt,sizeof(a->fs_prompt),"short1");render(a);tap_action(a,A_FS_PROMPT_NEXT);
    if(a->screen!=FILE_PROMPT){wrong++;fprintf(stderr,"a SuperKey of six characters was taken\n");}
    render(a);tap_action(a,A_NAV_BACK);
    if(a->screen!=ROOT_PAGE||strcmp(a->root_superkey,"Preview-Key-2026")||a->fs_prompt[0]){wrong++;fprintf(stderr,"Cancel on the SuperKey prompt leads to %d or lost the key\n",(int)a->screen);}
    render(a);tap_action(a,A_ROOT_KEY+1);copy(a->fs_prompt,sizeof(a->fs_prompt),"Good-Key-123");render(a);tap_action(a,A_FS_PROMPT_NEXT);
    if(a->screen!=ROOT_PAGE||strcmp(a->root_superkey,"Good-Key-123")||a->fs_prompt[0]){wrong++;fprintf(stderr,"a fitting SuperKey did not return to Root\n");}
    render(a);tap_action(a,A_NAV_BACK);
    if(a->screen!=ANDROID_MENU||a->root_superkey[0]){wrong++;fprintf(stderr,"leaving Root leads to %d or keeps the SuperKey\n",(int)a->screen);}
    /* 2026-11-11: Restore, Android: a copy opens its page, and Back returns to the list of copies. */
    preview(a,"restore-android");a->preview=true;screen(a,HOME);screen(a,LIST);render(a);tap_action(a,A_COPY_ITEM);
    if(a->screen!=COPY_DETAIL||a->copy_pick!=0){wrong++;fprintf(stderr,"a copy of Android did not open its page (%d)\n",(int)a->screen);}
    render(a);tap_action(a,A_COPY_PART+6);tap_action(a,A_NAV_BACK);
    if(a->screen!=LIST||!list_android(a)||a->copy_off!=(1u<<6)){wrong++;fprintf(stderr,"Back from a copy of Android leads to %d\n",(int)a->screen);}
    render(a);tap_action(a,A_LIST_CATEGORY);
    if(a->screen!=LIST||list_android(a)){wrong++;fprintf(stderr,"the Linux category of Restore did not open\n");}
    render(a);tap_action(a,A_NAV_BACK);
    if(a->screen!=HOME){wrong++;fprintf(stderr,"Back from Restore leads to %d\n",(int)a->screen);}
    /* New system on a phone with Android: "From a file" opens the sheet over the page; Back or Cancel
       closes the sheet and drops the choice, and only the next Back leaves the page. */
    preview(a,"new-system-android");a->preview=true;screen(a,HOME);screen(a,OS_MENU);screen(a,SYSTEMS);screen(a,SYSTEMS_NEW);render(a);
    if(!test_button(a,A_NEW_FILE)||!test_button(a,A_NEW_FILE)->enabled||test_button(a,A_SYS_MOUNT)||test_button(a,A_NEW_SAVED)->enabled){wrong++;fprintf(stderr,"New system on Android's userdata: wrong buttons\n");}
    /* Android's kernel is on BOOT there: the page says so and leads to the page that flashes BOOT, and back. */
    if(!a->systems_boot_android||!test_button(a,A_FLASH)){wrong++;fprintf(stderr,"New system does not offer to flash BOOT\n");}
    tap_action(a,A_FLASH);
    if(a->screen!=FLASH){wrong++;fprintf(stderr,"the BOOT hint leads to %d\n",(int)a->screen);}
    render(a);tap_action(a,A_NAV_BACK);
    if(a->screen!=SYSTEMS_NEW){wrong++;fprintf(stderr,"Back from Flash a partition leads to %d, not to New system\n",(int)a->screen);}
    render(a);
    tap_action(a,A_NEW_FILE);
    if(a->screen!=SYSTEMS_NEW||!a->source_sheet||!a->systems_pick_manifest||!a->systems_wipe){wrong++;fprintf(stderr,"From a file did not open the sheet\n");}
    render(a);if(!test_button(a,A_SOURCE_CANCEL)||test_button(a,A_NEW_INTERNET)){wrong++;fprintf(stderr,"the sheet left the page's buttons under it\n");}
    tap_action(a,A_NAV_BACK);
    if(a->screen!=SYSTEMS_NEW||a->source_sheet||a->systems_pick_manifest){wrong++;fprintf(stderr,"Back did not close the sheet\n");}
    render(a);tap_action(a,A_NEW_FILE);render(a);tap_action(a,A_SOURCE_CANCEL);
    if(a->screen!=SYSTEMS_NEW||a->source_sheet||a->systems_pick_manifest){wrong++;fprintf(stderr,"Cancel did not close the sheet\n");}
    render(a);tap_action(a,A_NAV_BACK);
    if(a->screen!=SYSTEMS){wrong++;fprintf(stderr,"Back from New system leads to %d\n",(int)a->screen);}
    /* The catalog opened to install Linux has no Android in it, and the one of Android no Linux. */
    preview(a,"android-catalog-all");a->preview=true;a->catalog_only=0;screen(a,HOME);screen(a,OS_MENU);screen(a,SYSTEMS);screen(a,SYSTEMS_NEW);render(a);tap_action(a,A_NEW_INTERNET);render(a);
    if(a->screen!=SYSTEMS_CATALOG||a->catalog_only!=1||a->catalog_category!=CATEGORY_LINUX||test_button(a,A_CATALOG_CATEGORY)||test_button(a,A_CATALOG_CATEGORY+1)){wrong++;fprintf(stderr,"the catalog of New system offers Android\n");}
    for(unsigned int n=0;n<a->catalog_count;n++)if(catalog_android(&a->catalog[n])&&test_button(a,A_CATALOG_ITEM+(int)n)){wrong++;fprintf(stderr,"an Android image is listed while Linux is installed\n");break;}
    screen(a,HOME);screen(a,OS_MENU);screen(a,ANDROID_MENU);render(a);tap_action(a,A_ANDROID_CATALOG);render(a);
    if(a->screen!=SYSTEMS_CATALOG||a->catalog_only!=2||a->catalog_category!=CATEGORY_ANDROID||test_button(a,A_CATALOG_CATEGORY)){wrong++;fprintf(stderr,"the catalog of Android offers Linux\n");}
    for(unsigned int n=0;n<a->catalog_count;n++)if(!catalog_android(&a->catalog[n])&&test_button(a,A_CATALOG_ITEM+(int)n)){wrong++;fprintf(stderr,"a Linux image is listed while Android is installed\n");break;}
    screen(a,HOME);render(a);tap_action(a,A_HOME_CATALOG);render(a);
    if(a->screen!=SYSTEMS_CATALOG||a->catalog_only||!test_button(a,A_CATALOG_CATEGORY)||!test_button(a,A_CATALOG_CATEGORY+1)){wrong++;fprintf(stderr,"the catalog of Home lost its two categories\n");}
    printf("{\"walk\":{\"edges\":%d,\"pages\":%d,\"wrong\":%d}}\n",edges,pages,wrong);
    free(a->pixels);free(a);
    return wrong||edges<100||pages<30?1:0;
}

#define NAV_CHECK(condition) do{if(!(condition)){fprintf(stderr,"test_nav_ui.c:%d: %s: page %d, job %d, returns to %d\n",__LINE__,#condition,(int)a->screen,a->job.kind,(int)a->result_return);return false;}}while(0)
static const char*inventory_reply="{\"ok\":true,\"capabilities\":{\"copy_directories\":true},\"volumes\":[{\"id\":\"cache\",\"label\":\"cache\",\"fs_type\":\"ext4\","
    "\"mountpoint\":\"/mnt/cache\",\"volume_token\":\"token-cache\",\"mounted\":true,\"read_only\":true,\"mountable\":true,\"write_supported\":true,\"size_bytes\":209715200}]}";
static const char*list_reply="{\"ok\":true,\"path\":\"/\",\"offset\":0,\"total\":2,\"has_more\":false,\"read_only\":true,\"entries\":["
    "{\"name\":\"AP_good.tar.md5\",\"path\":\"/AP_good.tar.md5\",\"type\":\"file\",\"size\":4096,\"entry_token\":\"e1\"},"
    "{\"name\":\"AP_bad.tar.md5\",\"path\":\"/AP_bad.tar.md5\",\"type\":\"file\",\"size\":4096,\"entry_token\":\"e2\"}]}";
static const char*review_reply="{\"ok\":true,\"build\":\"A226BXXSBDZC3\",\"plan_sha256\":\"1111111111111111111111111111111111111111111111111111111111111111\",\"write_bytes\":4096,"
    "\"rows\":[{\"name\":\"boot\",\"file\":\"boot.img.lz4\",\"action\":\"write\",\"target\":\"boot\",\"reason\":\"\",\"size\":4096}],\"files\":[{\"kind\":\"AP\"}],\"warnings\":[],\"blockers\":[]}";
static const char*refused_reply="{\"ok\":false,\"error\":{\"code\":\"AP_INCOMPLETE\",\"message\":\"The AP file has no super or no BOOT image.\"}}";
static const char*zip_reply="{\"ok\":true,\"operation\":\"inspect\",\"name\":\"AP_good.tar.md5\",\"sha256\":\"2222222222222222222222222222222222222222222222222222222222222222\","
    "\"size\":4096,\"installer\":\"script\",\"arch\":\"\",\"what\":\"other\",\"title\":\"\",\"files\":2,\"warnings\":[],\"partitions\":[\"boot\",\"dtbo\"]}";
static const char*boot_reply="{\"ok\":true,\"release\":\"4.14.186\",\"build\":\"A226BXXSBDZC3\",\"saved_boot\":\"/a22x-recovery/saved-boot/stock-A226BXXSBDZC3-5df570768942.img\",\"volume_token\":\"token-cache\"}";
static const char*targets_reply="{\"ok\":true,\"operation\":\"targets\",\"targets\":[{\"label\":\"boot\",\"title\":\"Kernel and ramdisk\",\"size_bytes\":33554432},"
    "{\"label\":\"recovery\",\"title\":\"This recovery\",\"size_bytes\":41943040}],\"super_free_bytes\":0}";
static const char*check_reply="{\"ok\":true,\"operation\":\"check\",\"target\":{\"label\":\"boot\",\"size_bytes\":33554432,\"offline\":true},"
    "\"image\":{\"name\":\"stock-A226BXXSBDZC3-5df570768942.img\",\"kind\":\"Android boot image\",\"sha256\":\"5df57076894215c64e0f7d4d3b722a8eb948355ad301efbc319122f8698916d6\","
    "\"identity\":\"0000000000000000000000000000000000000000000000000000000000000000\",\"file_bytes\":33554432,\"payload_bytes\":33554432,\"compression\":\"none\",\"format\":\"raw\"},"
    "\"warnings\":[],\"rollback_volume\":null,\"identical\":false}";

static App*fresh(const char*script,const char*work){
    App*a=calloc(1,sizeof(*a));if(!a)return NULL;initialize(a);a->device=false;a->vibration=0;a->now=milliseconds();
    copy(a->python,sizeof(a->python),script);copy(a->fixture,sizeof(a->fixture),work);screen(a,HOME);return a;
}
static void done(App*a){free(a->pixels);free(a);}
static bool press(App*a,int id){return test_press(a,id,40);}
static bool android_menu(App*a){return press(a,A_OS)&&a->screen==OS_MENU&&press(a,A_ANDROID)&&a->screen==ANDROID_MENU;}
/* Android > the picker of Samsung's firmware or of its BOOT, in the first folder of cache. */
static bool picker_folder(App*a,int entry){
    NAV_CHECK(android_menu(a)&&press(a,entry)&&a->screen==FILE_VOLUMES&&a->job.kind==J_FS_INVENTORY&&await_job(a));
    NAV_CHECK(a->screen==FILE_VOLUMES&&a->volume_count==1&&a->flash_pick&&a->samsung_pick);
    NAV_CHECK(press(a,A_FS_VOLUME_ITEM)&&await_job(a)&&a->screen==FILE_BROWSER&&a->file_count==2);
    return true;
}

/* Back pressed while the list of volumes is still read: kept, and made after the read. */
static bool back_during_read(App*a){
    NAV_CHECK(android_menu(a)&&press(a,A_STOCK_BOOT)&&a->screen==FILE_VOLUMES&&a->job.pid&&a->job.kind==J_FS_INVENTORY);
    NAV_CHECK(press(a,A_BACK)&&a->screen==FILE_VOLUMES&&a->job.pid&&a->queued_action==A_BACK);
    NAV_CHECK(await_job(a)&&a->screen==ANDROID_MENU&&!a->flash_pick&&!a->samsung_pick&&!a->stock_boot_pick&&!a->queued_action);
    NAV_CHECK(press(a,A_BACK)&&a->screen==OS_MENU&&press(a,A_BACK)&&a->screen==HOME);
    return true;
}
/* Home while the list is read: the list stays read, and no page opens over Home. */
static bool home_during_read(App*a){
    NAV_CHECK(android_menu(a)&&press(a,A_STOCK_BOOT)&&a->screen==FILE_VOLUMES&&a->job.pid);
    render(a);tap_action(a,A_NAV_HOME);
    NAV_CHECK(a->screen==HOME&&a->job.pid&&!a->flash_pick&&!a->samsung_pick);
    NAV_CHECK(await_job(a)&&a->screen==HOME&&a->volume_count==1);
    /* Files from Home afterwards is the plain list, and its Back is Home. */
    NAV_CHECK(press(a,A_FILES)&&await_job(a)&&a->screen==FILE_VOLUMES&&!a->flash_pick&&press(a,A_BACK)&&a->screen==HOME);
    return true;
}
/* Home swiped while the list is read, the finger still down when the read ends: the gesture is the
   bar's, not the page's, and is not dropped with the page's buttons. */
static bool home_swipe_over_read(App*a){
    NAV_CHECK(android_menu(a)&&press(a,A_STOCK_BOOT)&&a->screen==FILE_VOLUMES&&a->job.pid);
    render(a);uint64_t at=milliseconds();
    pointer(a,true,CW/2,CH-10,at);pointer(a,true,CW/2,CH-200,at+60);
    NAV_CHECK(a->gesture==GESTURE_BOTTOM&&a->gesture_ready);
    NAV_CHECK(await_job(a)&&a->screen==FILE_VOLUMES&&a->gesture==GESTURE_BOTTOM&&a->volume_count==1);
    render(a);pointer(a,false,CW/2,CH-200,milliseconds());
    NAV_CHECK(a->screen==HOME&&!a->flash_pick&&!a->samsung_pick);
    return true;
}
/* The review of a firmware returns to the folder the file was chosen in, and on from there. */
static bool review_to_folder(App*a){
    NAV_CHECK(picker_folder(a,A_SAMSUNG));
    NAV_CHECK(press(a,A_FS_ENTRY)&&await_job(a)&&a->screen==SAMSUNG_REVIEW&&a->samsung_known&&!a->flash_pick);
    NAV_CHECK(press(a,A_BACK)&&a->screen==FILE_BROWSER&&a->flash_pick&&a->samsung_pick&&!a->stock_boot_pick&&await_job(a)&&a->screen==FILE_BROWSER&&a->file_count==2);
    NAV_CHECK(press(a,A_BACK)&&a->screen==FILE_VOLUMES&&press(a,A_BACK)&&a->screen==ANDROID_MENU&&!a->flash_pick&&!a->samsung_pick);
    return true;
}
/* A file that is no firmware: the result returns to the folder, still choosing. */
static bool refusal_to_folder(App*a){
    NAV_CHECK(picker_folder(a,A_SAMSUNG));
    NAV_CHECK(press(a,A_FS_ENTRY+1)&&await_job(a)&&a->screen==RESULT&&!a->successful&&a->result_return==FILE_BROWSER&&a->flash_pick&&a->samsung_pick);
    NAV_CHECK(press(a,A_DONE)&&a->screen==FILE_BROWSER&&a->file_count==2&&a->flash_pick);
    NAV_CHECK(press(a,A_FS_ENTRY)&&await_job(a)&&a->screen==SAMSUNG_REVIEW);
    NAV_CHECK(press(a,A_BACK)&&await_job(a)&&a->screen==FILE_BROWSER&&press(a,A_BACK)&&a->screen==FILE_VOLUMES&&press(a,A_BACK)&&a->screen==ANDROID_MENU);
    return true;
}
/* A ZIP with an installer: its review returns to the folder it was chosen in, still choosing a ZIP. */
static bool zip_review_to_folder(App*a){
    NAV_CHECK(android_menu(a)&&press(a,A_ZIP)&&a->screen==FILE_VOLUMES&&a->zip_pick&&a->flash_pick&&!a->samsung_pick&&await_job(a));
    NAV_CHECK(press(a,A_FS_VOLUME_ITEM)&&await_job(a)&&a->screen==FILE_BROWSER&&a->file_count==2);
    NAV_CHECK(press(a,A_FS_ENTRY)&&await_job(a)&&a->screen==ZIP_REVIEW&&a->zip_known&&!a->zip_pick&&!a->flash_pick);
    NAV_CHECK(press(a,A_BACK)&&a->screen==FILE_BROWSER&&a->zip_pick&&a->flash_pick&&await_job(a));
    NAV_CHECK(press(a,A_BACK)&&a->screen==FILE_VOLUMES&&press(a,A_BACK)&&a->screen==ANDROID_MENU&&!a->zip_pick&&!a->flash_pick);
    return true;
}
/* Two ZIPs in a list (2026-11-11): the second is added from the folder of the first; leaving the picker
   that was opened for one more returns to the list; one hold runs both, one after the other. */
static bool zip_queue(App*a){
    NAV_CHECK(android_menu(a)&&press(a,A_ZIP)&&a->screen==FILE_VOLUMES&&await_job(a));
    NAV_CHECK(press(a,A_FS_VOLUME_ITEM)&&await_job(a)&&a->screen==FILE_BROWSER&&a->file_count==2);
    NAV_CHECK(press(a,A_FS_ENTRY)&&await_job(a)&&a->screen==ZIP_REVIEW&&a->zip_known&&a->zip_count==1);
    NAV_CHECK(press(a,A_ZIP_ADD)&&a->screen==FILE_BROWSER&&a->zip_adding&&a->zip_pick&&a->flash_pick&&await_job(a));
    NAV_CHECK(press(a,A_FS_ENTRY+1)&&await_job(a)&&a->screen==ZIP_REVIEW&&a->zip_count==2&&!a->zip_adding&&a->zip_known);
    NAV_CHECK(strcmp(a->zip_queue[0].path,a->zip_queue[1].path)!=0);
    /* The same file once more keeps the list as it is. */
    NAV_CHECK(press(a,A_ZIP_ADD)&&a->screen==FILE_BROWSER&&await_job(a)&&press(a,A_FS_ENTRY)&&await_job(a)&&a->screen==ZIP_REVIEW&&a->zip_count==2);
    /* One more is asked for and given up: back out of the folder and the volumes, and the list is there. */
    NAV_CHECK(press(a,A_ZIP_ADD)&&a->screen==FILE_BROWSER&&await_job(a));
    NAV_CHECK(press(a,A_BACK)&&a->screen==FILE_VOLUMES&&a->zip_adding&&press(a,A_BACK)&&a->screen==ZIP_REVIEW&&a->zip_count==2&&a->zip_known&&!a->zip_adding&&!a->flash_pick&&!a->zip_pick);
    /* One hold: the first installer, then the second, then the result with both. */
    NAV_CHECK(test_press(a,A_ZIP_CONFIRM,5100)&&a->job.pid&&a->job.kind==J_ZIP_RUN&&a->zip_at==0);
    for(int n=0;n<4&&(a->job.pid||a->screen!=RESULT);n++)NAV_CHECK(await_job(a)||a->screen==RESULT);
    NAV_CHECK(a->screen==RESULT&&a->successful&&a->zip_at==1&&a->zip_queue[0].state==1&&a->zip_queue[1].state==1&&a->zip_ran&&!a->zip_restart_at);
    NAV_CHECK(press(a,A_DONE)&&a->screen==ANDROID_MENU&&!a->zip_ran);
    return true;
}
/* The stock BOOT kept, then "Flash it to BOOT": the pages of Flash a partition return to Android. */
static bool kept_boot_to_android(App*a){
    NAV_CHECK(picker_folder(a,A_STOCK_BOOT)&&a->stock_boot_pick);
    NAV_CHECK(press(a,A_FS_ENTRY)&&await_job(a)&&a->screen==RESULT&&a->successful&&a->result_return==ANDROID_MENU&&a->stock_boot_path[0]);
    NAV_CHECK(press(a,A_STOCK_BOOT_FLASH)&&await_job(a)&&a->screen==FLASH_REVIEW&&a->flash_review_valid);
    NAV_CHECK(press(a,A_BACK)&&a->screen==FLASH&&!a->flash_pick&&await_job(a)&&press(a,A_BACK)&&a->screen==ANDROID_MENU);
    return true;
}
/* The catalog left while the state of userdata is read: the page of systems does not open over Home. */
static bool catalog_left(App*a){
    NAV_CHECK(press(a,A_HOME_CATALOG)&&a->screen==SYSTEMS_CATALOG&&a->job.pid&&a->job.kind==J_SYS_STATUS);
    NAV_CHECK(press(a,A_BACK)&&a->screen==HOME&&a->job.pid);
    NAV_CHECK(await_job(a)&&a->screen==HOME);
    return true;
}
/* The reason the phone stayed in recovery is not replaced by the inventory read at start-up. */
static bool refusal_kept(App*a){
    NAV_CHECK(start_job(a,J_INVENTORY));
    problem(a,"POWER_REFUSED","The phone stayed in recovery: test");
    NAV_CHECK(a->screen==RESULT&&await_job(a)&&a->inventory_ok);
    NAV_CHECK(a->screen==RESULT&&!a->successful&&!strcmp(a->status,"POWER_REFUSED")&&!strcmp(a->message,"The phone stayed in recovery: test"));
    NAV_CHECK(press(a,A_DONE)&&a->screen==HOME);
    return true;
}

/* 2026-11-11, found on the phone: Export diagnostics from Home reads the status and then the list
   of volumes. The second read is started by the end of the first, and must keep its own reply. */
static bool export_from_home(App*a){
    NAV_CHECK(!a->tools_known&&press(a,A_TOOLS_EXPORT)&&a->job.pid&&a->job.kind==J_TOOLS_STATUS&&a->screen==HOME);
    NAV_CHECK(await_job(a)&&a->screen==TOOLS_EXPORT&&a->tools_known&&a->logs_export_available&&a->volume_count==1&&!a->job.pid);
    NAV_CHECK(strcmp(a->status,"BACKEND_FAILED")!=0);
    NAV_CHECK(press(a,A_BACK)&&a->screen==HOME);
    return true;
}

/* 2026-11-11: a phone whose userdata holds Android keeps downloaded images on a card. The page asks
   the helper where the store is, downloads there, and reviews the installation from that card. */
static bool android_card_download(App*a){
    NAV_CHECK(preview(a,"android-image-nocard"));
    a->preview=false;a->saved_known=false;a->store_reason[0]=0;a->button_count=0;a->focus=-1;
    screen(a,HOME);screen(a,SYSTEMS_CATALOG);open_android_image(a,0);
    NAV_CHECK(a->screen==ANDROID_IMAGE&&a->job.pid&&a->job.kind==J_IMAGES&&!systems_ready(a));
    NAV_CHECK(await_job(a)&&a->saved_known&&store_on_card(a)&&!strcmp(a->store_volume,"sdcard-p1")&&!strcmp(a->store_token,"token-card")&&!a->saved_count);
    render(a);NAV_CHECK(test_button(a,A_ANDROID_DOWNLOAD)&&test_button(a,A_ANDROID_DOWNLOAD)->enabled&&!test_button(a,A_SYS_MOUNT));
    NAV_CHECK(press(a,A_ANDROID_DOWNLOAD)&&a->job.pid&&a->job.kind==J_IMAGES_DOWNLOAD&&await_job(a)&&a->screen==ANDROID_IMAGE);
    NAV_CHECK(a->saved_count==1&&android_saved(a,&a->catalog[0])&&strstr(a->saved_note,"aosp_arm64"));
    /* The review reads the file from the card: the stand-in answers only for that volume and its token. */
    NAV_CHECK(press(a,A_ANDROID_INSTALL)&&a->job.pid&&a->job.kind==J_SAMSUNG_INSPECT&&!strcmp(a->samsung_volume,"sdcard-p1")&&!strcmp(a->samsung_token,"token-card"));
    NAV_CHECK(await_job(a)&&a->screen==SAMSUNG_REVIEW&&a->samsung_known);
    NAV_CHECK(press(a,A_BACK)&&a->screen==ANDROID_IMAGE&&press(a,A_BACK)&&a->screen==SYSTEMS_CATALOG);
    return true;
}

/* 2026-11-11: a ZIP sideloaded over adb opens its review at once, as TWRP's sideload installs it;
   another file is named in a toast; nothing opens while a job runs or the phone is locked. */
static char sideload_dir[PATH_LEN];
static bool sideload_record(const char*name,const char*body){
    char path[PATH_LEN*2];snprintf(path,sizeof(path),"%s/%s",sideload_dir,name);
    FILE*f=fopen(path,"w");if(!f)return false;fputs(body,f);fclose(f);return true;
}
static bool sideload_opens_review(App*a){
    adb_dir=sideload_dir;mkdir(sideload_dir,0700);
    const char*zip="{\"file\":\"sideload-1.zip\",\"volume\":\"cache\",\"token\":\"tttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttt\",\"path\":\"/sideload/sideload-1.zip\",\"kind\":\"zip\",\"size\":4096}";
    NAV_CHECK(sideload_record("sideload-1.json",zip));
    /* Locked: it waits. */
    a->locked=true;a->now+=1000;sideload_poll(a);NAV_CHECK(!a->job.pid&&access("nonexistent",F_OK)!=0);
    char kept[PATH_LEN*2];snprintf(kept,sizeof(kept),"%s/sideload-1.json",sideload_dir);NAV_CHECK(access(kept,F_OK)==0);
    a->locked=false;screen(a,HOME);a->now+=1000;sideload_poll(a);
    NAV_CHECK(access(kept,F_OK)!=0&&a->job.pid&&a->job.kind==J_ZIP_INSPECT&&!strcmp(a->zip_volume,"cache")&&!strcmp(a->zip_path,"/sideload/sideload-1.zip"));
    NAV_CHECK(strstr(a->toast,"sideload-1.zip")!=NULL);
    NAV_CHECK(await_job(a)&&a->screen==ZIP_REVIEW&&a->zip_known);
    NAV_CHECK(press(a,A_BACK)&&a->screen==HOME);
    /* An image: a toast only. A broken record: read once and dropped. */
    NAV_CHECK(sideload_record("sideload-2.json","{\"file\":\"sideload-2-boot.img\",\"volume\":\"cache\",\"token\":\"t\",\"path\":\"/sideload/sideload-2-boot.img\",\"kind\":\"image\"}"));
    NAV_CHECK(sideload_record("sideload-3.json","not json"));
    a->now+=1000;sideload_poll(a);NAV_CHECK(!a->job.pid&&a->screen==HOME&&strstr(a->toast,"sideload-2-boot.img"));
    a->now+=1000;sideload_poll(a);NAV_CHECK(!a->job.pid&&a->screen==HOME);
    snprintf(kept,sizeof(kept),"%s/sideload-3.json",sideload_dir);NAV_CHECK(access(kept,F_OK)!=0);
    return true;
}

/* 2026-11-11, the owner's request. Flags beside the stand-in "python" change what it answers:
   .cards lists an SD card that is not mounted, .mounted is set by its mount, .installer makes the
   chosen file a ZIP with an installer. */
static char stand_in[PATH_LEN];
static void flag(const char*name,bool on){
    char path[PATH_LEN*2];snprintf(path,sizeof(path),"%s.%s",stand_in,name);
    if(on){FILE*f=fopen(path,"w");if(f)fclose(f);}else unlink(path);
}
static int listed(const App*a,const char*id){for(int n=0;n<a->volume_count;n++)if(!strcmp(a->volumes[n].id,id))return n;return -1;}
/* New system > From a file: the sheet lists the card, mounts it on a tap and opens its folders;
   Back from the top folder returns to the sheet. */
static bool sheet_mounts_the_card(App*a){
    flag("cards",true);flag("mounted",false);
    NAV_CHECK(preview(a,"new-system-android"));
    a->preview=false;a->button_count=0;a->focus=-1;screen(a,HOME);screen(a,OS_MENU);screen(a,SYSTEMS);screen(a,SYSTEMS_NEW);
    NAV_CHECK(press(a,A_NEW_FILE)&&a->screen==SYSTEMS_NEW&&a->source_sheet&&a->job.pid&&a->job.kind==J_FS_INVENTORY);
    NAV_CHECK(await_job(a)&&a->screen==SYSTEMS_NEW&&a->source_sheet&&a->source_known&&a->volume_count==2);
    int card=listed(a,"sdcard-p1"),cache=listed(a,"cache");NAV_CHECK(card>=0&&cache>=0&&!a->volumes[card].mounted);
    render(a);NAV_CHECK(test_button(a,A_SOURCE_ITEM+card)&&!test_button(a,A_SOURCE_ITEM+cache)); /* a Linux image comes from a card */
    NAV_CHECK(press(a,A_SOURCE_ITEM+card)&&!a->source_sheet&&a->job.pid&&a->job.kind==J_FS_MOUNT&&!strcmp(a->fs_request.mode,"ro"));
    NAV_CHECK(await_job(a)&&a->screen==FILE_BROWSER&&a->file_count==2&&a->systems_pick_manifest&&current_volume(a)&&current_volume(a)->mounted);
    NAV_CHECK(press(a,A_BACK)&&a->screen==SYSTEMS_NEW&&a->source_sheet&&a->systems_pick_manifest&&await_job(a)&&a->source_known);
    /* Mounted now: a tap opens it at once. */
    card=listed(a,"sdcard-p1");NAV_CHECK(card>=0&&a->volumes[card].mounted&&press(a,A_SOURCE_ITEM+card)&&await_job(a)&&a->screen==FILE_BROWSER);
    NAV_CHECK(press(a,A_SYS_CANCEL_PICK)&&a->screen==SYSTEMS_NEW&&!a->systems_pick_manifest&&!a->source_sheet);
    NAV_CHECK(press(a,A_BACK)&&a->screen==SYSTEMS);
    flag("cards",false);flag("mounted",false);
    return true;
}
/* Android > ROM from a file: what the file is decides which review opens; each returns to the folder. */
static bool rom_from_a_file(App*a){
    flag("installer",false);
    NAV_CHECK(android_menu(a)&&press(a,A_ANDROID_FILE)&&a->screen==ANDROID_MENU&&a->source_sheet&&a->rom_pick&&a->flash_pick&&a->job.kind==J_FS_INVENTORY);
    NAV_CHECK(await_job(a)&&a->source_known&&a->volume_count==1);
    NAV_CHECK(press(a,A_SOURCE_ITEM)&&!a->source_sheet&&await_job(a)&&a->screen==FILE_BROWSER&&a->file_count==2&&a->rom_pick);
    /* A system image: the review of a GSI, read from the file where it lies. */
    NAV_CHECK(press(a,A_FS_ENTRY)&&a->job.pid&&a->job.kind==J_ROM_KIND&&!a->rom_pick&&a->screen==FILE_BROWSER);
    NAV_CHECK(await_job(a)&&a->screen==SAMSUNG_REVIEW&&a->samsung_known&&a->android_gsi&&a->gsi_file&&!strcmp(a->samsung_volume,"cache")&&!strcmp(a->samsung_path,"/AP_good.tar.md5"));
    NAV_CHECK(press(a,A_BACK)&&a->screen==FILE_BROWSER&&a->rom_pick&&a->flash_pick&&!a->samsung_pick&&await_job(a)&&a->file_count==2);
    /* Nothing that can be installed: said so, and the folder again. */
    NAV_CHECK(press(a,A_FS_ENTRY+1)&&await_job(a)&&a->screen==RESULT&&!a->successful&&!strcmp(a->status,"NOT_A_ROM")&&a->result_return==FILE_BROWSER&&a->rom_pick);
    NAV_CHECK(press(a,A_DONE)&&a->screen==FILE_BROWSER&&a->rom_pick);
    /* A ZIP with its own installer: the ZIP page. */
    flag("installer",true);
    NAV_CHECK(press(a,A_FS_ENTRY)&&await_job(a)&&a->screen==ZIP_REVIEW&&a->zip_known&&!strcmp(a->zip_volume,"cache")&&!strcmp(a->zip_path,"/AP_good.tar.md5")&&!a->rom_pick);
    NAV_CHECK(press(a,A_BACK)&&a->screen==FILE_BROWSER&&a->rom_pick&&!a->zip_pick&&await_job(a));
    flag("installer",false);
    /* Back from the top folder: the sheet again, over the Android page; Cancel gives the choice up. */
    NAV_CHECK(press(a,A_BACK)&&a->screen==ANDROID_MENU&&a->source_sheet&&a->rom_pick&&await_job(a));
    NAV_CHECK(press(a,A_SOURCE_CANCEL)&&a->screen==ANDROID_MENU&&!a->source_sheet&&!a->rom_pick&&!a->flash_pick);
    NAV_CHECK(press(a,A_BACK)&&a->screen==OS_MENU);
    return true;
}
/* Restore > Android: the copies on the cards, the page of one, its check and its restore with one
   partition left out; a card that is not mounted is mounted and searched. */
static bool android_copies(App*a){
    flag("mounted",false);
    NAV_CHECK(press(a,A_RESTORE)&&await_job(a)&&a->screen==LIST&&!list_android(a));
    NAV_CHECK(press(a,A_LIST_CATEGORY+1)&&a->screen==LIST&&list_android(a)&&a->job.pid&&a->job.kind==J_COPY_LIST);
    NAV_CHECK(await_job(a)&&a->copies_known&&a->copy_count==1&&a->copy_waiting_count==1&&a->copies[0].archive&&a->copies[0].parts==2&&!a->copies[0].writable);
    NAV_CHECK(press(a,A_COPY_ITEM)&&a->screen==COPY_DETAIL&&copy_current(a));
    render(a);NAV_CHECK(test_button(a,A_COPY_DELETE)&&!test_button(a,A_COPY_DELETE)->enabled&&test_button(a,A_COPY_RESTORE)->enabled&&test_button(a,A_COPY_RESTORE)->hold_ms==3000);
    NAV_CHECK(press(a,A_COPY_VERIFY)&&a->job.kind==J_COPY_VERIFY&&await_job(a)&&a->screen==RESULT&&a->successful&&a->result_return==COPY_DETAIL&&strstr(a->message,"vbmeta"));
    NAV_CHECK(press(a,A_DONE)&&a->screen==COPY_DETAIL);
    /* super left out: the helper is told to restore boot only (the stand-in answers to nothing else). */
    NAV_CHECK(press(a,A_COPY_PART+1)&&a->copy_off==2u&&a->screen==COPY_DETAIL);
    NAV_CHECK(test_press(a,A_COPY_RESTORE,1500)&&!a->job.pid&&a->screen==COPY_DETAIL); /* a short hold starts nothing */
    NAV_CHECK(test_press(a,A_COPY_RESTORE,3200)&&a->job.pid&&a->job.kind==J_COPY_RESTORE&&!strcmp(a->copy_labels_arg,"boot"));
    NAV_CHECK(await_job(a)&&a->screen==RESULT&&a->successful&&!strcmp(a->status,"Android restored")&&strstr(a->message,"boot")&&strstr(a->message,"saved-boot"));
    NAV_CHECK(press(a,A_DONE)&&a->screen==COPY_DETAIL&&press(a,A_BACK)&&a->screen==LIST&&list_android(a));
    /* The card that was not mounted: mounted read-only, and the list read again with what it holds. */
    NAV_CHECK(press(a,A_COPY_MOUNT)&&a->job.pid&&a->job.kind==J_FS_MOUNT&&a->copy_mounting&&!strcmp(a->fs_request.volume,"sdcard-p1")&&!strcmp(a->fs_request.mode,"ro"));
    NAV_CHECK(await_job(a)&&a->screen==LIST&&list_android(a)&&a->copies_known&&a->copy_count==2&&!a->copy_waiting_count&&a->copies[1].writable&&!a->copies[1].archive);
    /* The folder copy on the card mounted for writing may be deleted; the list is read again. */
    NAV_CHECK(press(a,A_COPY_ITEM+1)&&a->screen==COPY_DETAIL&&test_press(a,A_COPY_DELETE,3200)&&a->job.kind==J_COPY_DELETE);
    NAV_CHECK(await_job(a)&&a->screen==LIST&&a->copies_known&&a->copy_pick<0);
    /* Home, and Restore again: the category last used opens at once. */
    NAV_CHECK(press(a,A_BACK)&&a->screen==HOME&&press(a,A_VERIFY)&&a->screen==LIST&&list_android(a)&&a->list_for_verify&&await_job(a));
    flag("mounted",false);
    return true;
}

int main(int argc,char**argv){
    if(argc!=2)return 2;
    snprintf(sideload_dir,sizeof(sideload_dir),"%s/adb",argv[1]);
    int failed=walk();if(failed==2)return 2;
    char script[PATH_LEN];snprintf(script,sizeof(script),"%s/fake-python",argv[1]);copy(stand_in,sizeof(stand_in),script);
    flag("cards",false);flag("mounted",false);flag("installer",false);
    FILE*f=fopen(script,"w");if(!f)return 2;
    /* What the flags change: the volumes, the kind of a chosen file, the copies of Android. */
    fputs("#!/bin/sh\n"
        "card() { echo '{\"ok\":true,\"capabilities\":{\"copy_directories\":true},\"volumes\":[{\"id\":\"cache\",\"label\":\"cache\",\"fs_type\":\"ext4\",\"mountpoint\":\"/mnt/cache\",\"volume_token\":\"token-cache\",\"mounted\":true,\"read_only\":true,\"mountable\":true,\"write_supported\":true,\"size_bytes\":209715200},'"
        "'{\"id\":\"sdcard-p1\",\"label\":\"card\",\"fs_type\":\"ext4\",\"mountpoint\":\"/mnt/card\",\"volume_token\":\"token-card\",\"mounted\":'$1',\"read_only\":true,\"mountable\":true,\"write_supported\":true,\"size_bytes\":15707668480}]}'; }\n"
        "copy() { echo '{\"path\":\"/A22xAndroidBackups/'$1'\",\"created_utc\":\"2026-11-1'$2'T08:10:50Z\",\"same_phone\":true,\"partitions\":2,\"manifest_sha256\":\"'$3'\",\"bytes\":7272923136,\"labels\":[\"boot\",\"super\"],\"archive\":'$4',\"file_bytes\":'$5',\"volume\":\"'$6'\",\"volume_token\":\"token\",\"volume_label\":\"'$6'\",\"writable\":'$7'}'; }\n"
        "case \"$*\" in\n"
        "*filesystem.py*' mount '*) : > \"$0.mounted\"; echo '{\"ok\":true}'; exit 0;;\n"
        "*filesystem.py*' inventory'*) if [ -f \"$0.cards\" ]; then sleep 0.05; if [ -f \"$0.mounted\" ]; then card true; else card false; fi; exit 0; fi;;\n"
        "*android_gsi.py*' classify '*bad*) echo '{\"ok\":true,\"kind\":\"unknown\"}'; exit 0;;\n"
        "*android_gsi.py*' classify '*) if [ -f \"$0.installer\" ]; then echo '{\"ok\":true,\"kind\":\"installer\"}'; else echo '{\"ok\":true,\"kind\":\"gsi\"}'; fi; exit 0;;\n"
        "*identity_backup.py*'--kind android copies') sleep 0.05; a=$(copy android-20261111-081050.tar.zst 1 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa true 3865470566 usb-p1 false)\n"
        "  if [ -f \"$0.mounted\" ]; then b=$(copy android-20261110-081050 0 bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb false null sdcard-p1 true); echo '{\"ok\":true,\"backups\":['$a','$b'],\"unmounted\":[],\"folder\":\"A22xAndroidBackups\"}'\n"
        "  else echo '{\"ok\":true,\"backups\":['$a'],\"unmounted\":[{\"volume\":\"sdcard-p1\",\"volume_token\":\"token-card\",\"volume_label\":\"card\",\"fs_type\":\"ext4\",\"size_bytes\":15707668480}],\"folder\":\"A22xAndroidBackups\"}'; fi; exit 0;;\n"
        "*identity_backup.py*'--kind android verify --volume usb-p1 --path /A22xAndroidBackups/android-20261111-081050.tar.zst') echo '{\"ok\":true,\"verified\":true,\"restorable\":true,\"archive\":true,\"partitions\":[{\"label\":\"boot\",\"size_bytes\":33554432},{\"label\":\"super\",\"size_bytes\":7239368704}],\"locked\":[\"vbmeta\"],\"vbmeta_blocks\":false}'; exit 0;;\n"
        "*identity_backup.py*'--kind android --labels boot restore --volume usb-p1 --path /A22xAndroidBackups/android-20261111-081050.tar.zst --expected-manifest-sha256 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa --confirm') echo '{\"ok\":true,\"restored\":[\"boot\"],\"unchanged\":[],\"left_locked\":[],\"identical\":false,\"before\":null,\"kept_boot\":\"/a22x-recovery/saved-boot/linux-7.2.0-a22x-0123456789ab.img\",\"keeps_recovery\":true,\"written_bytes\":1048576}'; exit 0;;\n"
        "*identity_backup.py*'--kind android delete --volume sdcard-p1 --path /A22xAndroidBackups/android-20261110-081050 --expected-manifest-sha256 bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb --confirm') echo '{\"ok\":true,\"deleted\":\"/A22xAndroidBackups/android-20261110-081050\",\"partitions\":2,\"bytes\":1}'; exit 0;;\n"
        "*identity_backup.py*) exit 9;;\n"
        "esac\n",f);
    fprintf(f,"case \"$*\" in\n"
        "*filesystem.py*' inventory'*) sleep 0.25; echo '%s';;\n"
        "*filesystem.py*' list '*) sleep 0.05; echo '%s';;\n"
        "*samsung_firmware.py*' inspect '*bad*) echo '%s'; exit 1;;\n"
        "*samsung_firmware.py*' inspect '*) echo '%s';;\n"
        "*samsung_firmware.py*' extract-boot '*) echo '%s';;\n"
        "*zip_install.py*' run '*) sleep 0.05; echo '{\"ok\":true,\"operation\":\"run\",\"changed\":[\"boot\"],\"log\":[\"done\"]}';;\n"
        "*zip_install.py*' inspect '*) echo '%s';;\n"
        "*flash.py*' targets'*) sleep 0.1; echo '%s';;\n"
        "*flash.py*' check '*) echo '%s';;\n"
        "*recoveryctl.py*) sleep 0.1; echo '{\"ok\":true}';;\n"
        "*distro.py*' images-download '*) sleep 0.1; echo '{\"ok\":true,\"saved\":\"aosp_arm64-exp-TQ3A.230805.001.A3-10454027-66e107ed.zip\",\"free_bytes\":3000000000,\"store_volume\":\"sdcard-p1\",\"store_token\":\"token-card\",\"store_label\":\"SD\",\"images\":[{\"file\":\"aosp_arm64-exp-TQ3A.230805.001.A3-10454027-66e107ed.zip\",\"path\":\"/.a22x-images/aosp_arm64-exp-TQ3A.230805.001.A3-10454027-66e107ed.zip\",\"size\":832870349,\"family\":\"android\",\"name\":\"Android 13\",\"version\":\"TEST\",\"checked\":true}]}';;\n"
        "*distro.py*' images'*) sleep 0.1; echo '{\"ok\":true,\"images\":[],\"free_bytes\":4000000000,\"store\":\"/.a22x-images\",\"store_volume\":\"sdcard-p1\",\"store_token\":\"token-card\",\"store_label\":\"SD\"}';;\n"
        "*android_gsi.py*' inspect '*'--volume sdcard-p1 --volume-token token-card '*) echo '%s';;\n"
        "*android_gsi.py*' inspect '*'--volume cache --volume-token token-cache --path /AP_good.tar.md5'*) echo '%s';;\n"
        "*management.py*' status'*) sleep 0.1; echo '{\"ok\":true,\"ssh\":{\"available\":false},\"logs\":{\"export_available\":true}}';;\n"
        "*) sleep 0.25; exit 9;;\nesac\n",inventory_reply,list_reply,refused_reply,review_reply,boot_reply,zip_reply,targets_reply,check_reply,review_reply,review_reply);
    fclose(f);chmod(script,0700);
    static const struct{const char*name;bool(*run)(App*);}cases[]={{"back-during-read",back_during_read},{"home-during-read",home_during_read},{"home-swipe-over-read",home_swipe_over_read},{"review-to-folder",review_to_folder},
        {"refusal-to-folder",refusal_to_folder},{"zip-review-to-folder",zip_review_to_folder},{"kept-boot-to-android",kept_boot_to_android},{"catalog-left",catalog_left},{"refusal-kept",refusal_kept},{"export-from-home",export_from_home},{"android-card-download",android_card_download},{"sideload-opens-review",sideload_opens_review},
        {"sheet-mounts-the-card",sheet_mounts_the_card},{"rom-from-a-file",rom_from_a_file},{"android-copies",android_copies},{"zip-queue",zip_queue}};
    for(size_t n=0;n<sizeof(cases)/sizeof(*cases);n++){
        App*a=fresh(script,argv[1]);if(!a)return 2;
        bool good=cases[n].run(a);if(!good)failed=1;
        printf("{\"case\":\"%s\",\"ok\":%s,\"page\":%d}\n",cases[n].name,good?"true":"false",(int)a->screen);done(a);
    }
    printf("{\"ok\":%s}\n",failed?"false":"true");
    return failed?1:0;
}
