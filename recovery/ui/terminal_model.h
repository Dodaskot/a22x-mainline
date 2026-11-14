/* SPDX-License-Identifier: MIT
 * Bounded UTF-8 / ANSI terminal grid. This is a VT100-oriented subset, not a
 * complete xterm: no mouse, clipboard/OSC actions, sixel, printer or reflow.
 * Control semantics reference https://invisible-island.net/xterm/ctlseqs/ .
 */
#ifndef A22X_TERMINAL_MODEL_H
#define A22X_TERMINAL_MODEL_H
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>

#define TM_MAX_COLS 80
#define TM_MAX_ROWS 60
#define TM_HISTORY 512
#define TM_PARAMS 24
#define TM_DEFAULT_FG 0xe5e9efu
#define TM_DEFAULT_BG 0x0b0e13u
#define TM_ANSI_PALETTE {0x101318,0xe06c75,0x98c379,0xe5c07b,0x61afef,0xc678dd,0x56b6c2,0xd7dce2,\
    0x7f8996,0xff8e95,0xb5e697,0xffdc92,0x8bc8ff,0xe7a7ff,0x8fe1e9,0xffffff}
#define TM_BOLD 1u
#define TM_DIM 2u
#define TM_UNDERLINE 4u
#define TM_INVERSE 8u
#define TM_HIDDEN 16u

typedef struct {uint32_t cp,mark,fg,bg;uint8_t flags,width;} TmCell;
typedef struct {TmCell cells[TM_MAX_COLS];} TmLine;
typedef struct {uint32_t fg,bg;uint8_t flags;} TmStyle;
typedef struct {
    TmLine lines[TM_MAX_ROWS];
    unsigned row,col,saved_row,saved_col,top,bottom;
    TmStyle style,saved_style;
    bool wrap_pending,origin;
} TmPage;
typedef struct {
    TmPage pages[2];TmLine history[TM_HISTORY];
    unsigned rows,cols,page,history_first,history_count;
    uint64_t dropped_lines,generation;
    bool cursor_visible,autowrap,insert,newline,application_cursor;
    bool tabs[TM_MAX_COLS],graphics[2],shift_out;
    unsigned parser,param_count,params[TM_PARAMS],sequence_bytes,charset;
    bool private_csi,ignore_csi,param_present;
    uint32_t utf_value,utf_min;unsigned utf_left;
    char replies[256];size_t reply_length;
} TerminalModel;

enum {TM_GROUND,TM_ESCAPE,TM_CSI,TM_OSC,TM_STRING,TM_STRING_ESC,TM_CHARSET,TM_ESC_IGNORE};

static inline TmPage *tm_page(TerminalModel*t){return &t->pages[t->page];}
static inline const TmPage *tm_const_page(const TerminalModel*t){return &t->pages[t->page];}
static inline unsigned tm_min(unsigned a,unsigned b){return a<b?a:b;}
static inline TmCell tm_blank(TmStyle s){return(TmCell){.cp=' ',.fg=s.fg,.bg=s.bg,.flags=s.flags,.width=1};}
static inline TmStyle tm_default_style(void){return(TmStyle){TM_DEFAULT_FG,TM_DEFAULT_BG,0};}
static inline void tm_clear_line(TerminalModel*t,TmLine*line,TmStyle style){
    for(unsigned c=0;c<t->cols;c++)line->cells[c]=tm_blank(style);
}
static inline void tm_reset_page(TerminalModel*t,TmPage*p){
    memset(p,0,sizeof(*p));p->style=p->saved_style=tm_default_style();p->bottom=t->rows-1;
    for(unsigned r=0;r<t->rows;r++)tm_clear_line(t,&p->lines[r],p->style);
}
static inline bool tm_init(TerminalModel*t,unsigned rows,unsigned cols){
    if(!t||!rows||rows>TM_MAX_ROWS||!cols||cols>TM_MAX_COLS)return false;
    memset(t,0,sizeof(*t));t->rows=rows;t->cols=cols;t->autowrap=t->cursor_visible=true;
    for(unsigned c=8;c<cols;c+=8)t->tabs[c]=true;
    tm_reset_page(t,&t->pages[0]);tm_reset_page(t,&t->pages[1]);return true;
}
static inline unsigned tm_line_count(const TerminalModel*t){return(t->page?0:t->history_count)+t->rows;}
static inline const TmLine*tm_line(const TerminalModel*t,unsigned row){
    unsigned history=t->page?0:t->history_count;
    if(row<history)return &t->history[(t->history_first+row)%TM_HISTORY];
    row-=history;return row<t->rows?&tm_const_page(t)->lines[row]:NULL;
}
static inline void tm_history_push(TerminalModel*t,const TmLine*line){
    unsigned index=(t->history_first+t->history_count)%TM_HISTORY;
    if(t->history_count==TM_HISTORY){index=t->history_first;t->history_first=(t->history_first+1)%TM_HISTORY;t->dropped_lines++;}
    else t->history_count++;
    t->history[index]=*line;
}
static inline void tm_scroll_up(TerminalModel*t,unsigned top,unsigned bottom,unsigned count,bool history){
    TmPage*p=tm_page(t);if(top>bottom||bottom>=t->rows)return;
    count=tm_min(count,bottom-top+1);
    for(unsigned n=0;n<count;n++){
        if(history&&!t->page&&top==0&&bottom==t->rows-1)tm_history_push(t,&p->lines[top]);
        if(bottom>top)memmove(&p->lines[top],&p->lines[top+1],(bottom-top)*sizeof(TmLine));
        tm_clear_line(t,&p->lines[bottom],p->style);
    }
}
static inline void tm_scroll_down(TerminalModel*t,unsigned top,unsigned bottom,unsigned count){
    TmPage*p=tm_page(t);if(top>bottom||bottom>=t->rows)return;
    count=tm_min(count,bottom-top+1);
    for(unsigned n=0;n<count;n++){
        if(bottom>top)memmove(&p->lines[top+1],&p->lines[top],(bottom-top)*sizeof(TmLine));
        tm_clear_line(t,&p->lines[top],p->style);
    }
}
static inline void tm_index(TerminalModel*t){
    TmPage*p=tm_page(t);p->wrap_pending=false;
    if(p->row==p->bottom)tm_scroll_up(t,p->top,p->bottom,1,true);
    else if(p->row+1<t->rows)p->row++;
}
static inline void tm_reverse_index(TerminalModel*t){
    TmPage*p=tm_page(t);p->wrap_pending=false;
    if(p->row==p->top)tm_scroll_down(t,p->top,p->bottom,1);else if(p->row)p->row--;
}
static inline void tm_erase(TerminalModel*t,unsigned row,unsigned first,unsigned end){
    if(row>=t->rows||first>=t->cols)return;
    end=tm_min(end,t->cols);TmPage*p=tm_page(t);TmLine*line=&p->lines[row];
    if(first&&line->cells[first].width==0)line->cells[first-1]=tm_blank(p->style);
    if(end<t->cols&&end&&line->cells[end-1].width==2)line->cells[end]=tm_blank(p->style);
    for(unsigned c=first;c<end;c++)line->cells[c]=tm_blank(p->style);
}
static inline void tm_fix_wide(TerminalModel*t,TmLine*line){
    TmStyle style=tm_page(t)->style;
    for(unsigned c=0;c<t->cols;c++){
        if(!line->cells[c].width&&(c==0||line->cells[c-1].width!=2))line->cells[c]=tm_blank(style);
        if(line->cells[c].width==2&&(c+1==t->cols||line->cells[c+1].width!=0))line->cells[c]=tm_blank(style);
    }
}
static inline unsigned tm_width(uint32_t cp){
    if((cp>=0x300&&cp<=0x36f)||(cp>=0x1ab0&&cp<=0x1aff)||(cp>=0x1dc0&&cp<=0x1dff)||
       (cp>=0x20d0&&cp<=0x20ff)||(cp>=0xfe00&&cp<=0xfe0f)||(cp>=0xfe20&&cp<=0xfe2f)||cp==0x200d)return 0;
    return ((cp>=0x1100&&cp<=0x115f)||(cp>=0x2e80&&cp<=0xa4cf)||(cp>=0xac00&&cp<=0xd7a3)||
            (cp>=0xf900&&cp<=0xfaff)||(cp>=0xfe10&&cp<=0xfe19)||(cp>=0xfe30&&cp<=0xfe6f)||
            (cp>=0xff01&&cp<=0xff60)||(cp>=0xffe0&&cp<=0xffe6)||(cp>=0x1f300&&cp<=0x1faff)||
            (cp>=0x20000&&cp<=0x3fffd))?2:1;
}
static inline uint32_t tm_graphic(uint32_t cp){
    static const uint32_t chars[31]={0x25c6,0x2592,0x2409,0x240c,0x240d,0x240a,0xb0,0xb1,
        0x2424,0x240b,0x2518,0x2510,0x250c,0x2514,0x253c,0x23ba,0x23bb,0x2500,0x23bc,0x23bd,
        0x251c,0x2524,0x2534,0x252c,0x2502,0x2264,0x2265,0x3c0,0x2260,0xa3,0xb7};
    return cp>=0x60&&cp<=0x7e?chars[cp-0x60]:cp;
}
static inline void tm_put(TerminalModel*t,uint32_t cp){
    TmPage*p=tm_page(t);if(t->graphics[t->shift_out?1:0]&&cp<0x80)cp=tm_graphic(cp);
    unsigned width=tm_width(cp);
    if(!width){
        unsigned col=p->wrap_pending?p->col:(p->col?p->col-1:0);
        if(!p->lines[p->row].cells[col].width&&col)col--;
        p->lines[p->row].cells[col].mark=cp;t->generation++;return;
    }
    if(p->wrap_pending&&t->autowrap){p->col=0;tm_index(t);}
    p->wrap_pending=false;
    if(width==2&&p->col+1>=t->cols){if(t->autowrap&&t->cols>1){p->col=0;tm_index(t);}else{cp=0xfffd;width=1;}}
    TmLine*line=&p->lines[p->row];
    if(t->insert){unsigned count=tm_min(width,t->cols-p->col);if(p->col+count<t->cols)memmove(&line->cells[p->col+count],&line->cells[p->col],(t->cols-p->col-count)*sizeof(TmCell));tm_fix_wide(t,line);}
    tm_erase(t,p->row,p->col,p->col+width);
    line->cells[p->col]=(TmCell){.cp=cp,.fg=p->style.fg,.bg=p->style.bg,.flags=p->style.flags,.width=(uint8_t)width};
    if(width==2)line->cells[p->col+1]=(TmCell){.fg=p->style.fg,.bg=p->style.bg,.flags=p->style.flags,.width=0};
    if(p->col+width>=t->cols){p->col=t->cols-1;p->wrap_pending=t->autowrap;}else p->col+=width;
    t->generation++;
}
static inline void tm_save_cursor(TerminalModel*t){TmPage*p=tm_page(t);p->saved_row=p->row;p->saved_col=p->col;p->saved_style=p->style;}
static inline void tm_restore_cursor(TerminalModel*t){TmPage*p=tm_page(t);p->row=tm_min(p->saved_row,t->rows-1);p->col=tm_min(p->saved_col,t->cols-1);p->style=p->saved_style;p->wrap_pending=false;}
static inline void tm_position(TerminalModel*t,unsigned row,unsigned col){
    TmPage*p=tm_page(t);unsigned top=p->origin?p->top:0,bottom=p->origin?p->bottom:t->rows-1;
    p->row=top+tm_min(row,bottom-top);p->col=tm_min(col,t->cols-1);p->wrap_pending=false;
}
static inline uint32_t tm_color(unsigned index){
    static const uint32_t colors[16]=TM_ANSI_PALETTE;
    if(index<16)return colors[index];
    if(index>=232){unsigned c=8+10*tm_min(index-232,23);return(c<<16)|(c<<8)|c;}
    index-=16;unsigned r=index/36,g=(index/6)%6,b=index%6;r=r?55+40*r:0;g=g?55+40*g:0;b=b?55+40*b:0;
    return(r<<16)|(g<<8)|b;
}
static inline unsigned tm_param(const TerminalModel*t,unsigned index,unsigned fallback){return index<t->param_count&&t->params[index]?t->params[index]:fallback;}
static inline void tm_sgr(TerminalModel*t){
    TmStyle*s=&tm_page(t)->style;
    for(unsigned i=0;i<t->param_count;i++){
        unsigned p=t->params[i];
        if(!p)*s=tm_default_style();else if(p==1)s->flags|=TM_BOLD;else if(p==2)s->flags|=TM_DIM;
        else if(p==4)s->flags|=TM_UNDERLINE;else if(p==7)s->flags|=TM_INVERSE;else if(p==8)s->flags|=TM_HIDDEN;
        else if(p==22)s->flags&=~(TM_BOLD|TM_DIM);else if(p==24)s->flags&=~TM_UNDERLINE;else if(p==27)s->flags&=~TM_INVERSE;else if(p==28)s->flags&=~TM_HIDDEN;
        else if(p>=30&&p<=37)s->fg=tm_color(p-30);else if(p>=40&&p<=47)s->bg=tm_color(p-40);
        else if(p>=90&&p<=97)s->fg=tm_color(p-90+8);else if(p>=100&&p<=107)s->bg=tm_color(p-100+8);
        else if(p==39)s->fg=TM_DEFAULT_FG;else if(p==49)s->bg=TM_DEFAULT_BG;
        else if(p==38||p==48){
            uint32_t color=0;bool valid=false;
            if(i+2<t->param_count&&t->params[i+1]==5){color=tm_color(tm_min(t->params[i+2],255));i+=2;valid=true;}
            else if(i+4<t->param_count&&t->params[i+1]==2){color=(tm_min(t->params[i+2],255)<<16)|(tm_min(t->params[i+3],255)<<8)|tm_min(t->params[i+4],255);i+=4;valid=true;}
            if(valid){if(p==38)s->fg=color;else s->bg=color;}
        }
    }
}
static inline void tm_reply(TerminalModel*t,const char*s){size_t n=strlen(s);if(n<=sizeof(t->replies)-t->reply_length){memcpy(t->replies+t->reply_length,s,n);t->reply_length+=n;}}
static inline void tm_modes(TerminalModel*t,bool set){
    for(unsigned i=0;i<t->param_count;i++){
        unsigned mode=t->params[i];TmPage*p=tm_page(t);
        if(t->private_csi){
            if(mode==1)t->application_cursor=set;
            else if(mode==6){p->origin=set;tm_position(t,0,0);}
            else if(mode==7){t->autowrap=set;p->wrap_pending=false;}
            else if(mode==25)t->cursor_visible=set;
            else if(mode==1048){if(set)tm_save_cursor(t);else tm_restore_cursor(t);}
            else if(mode==47||mode==1047||mode==1049){
                if(set&&!t->page){if(mode==1049)tm_save_cursor(t);t->page=1;tm_reset_page(t,tm_page(t));}
                else if(!set&&t->page){t->page=0;if(mode==1049)tm_restore_cursor(t);}
            }
        }else if(mode==4)t->insert=set;else if(mode==20)t->newline=set;
    }
}
static inline void tm_csi(TerminalModel*t,unsigned char final){
    if(t->ignore_csi)return;
    TmPage*p=tm_page(t);unsigned n=tm_param(t,0,1),zero=t->params[0];
    /* As xterm's CursorUp and CursorDown: up stops at the top margin from any row at or below it,
       down at the bottom margin from any row at or above it; past a margin the screen's edge
       (2026-11-14 reviews: ESC[B at the region's foot left it, and the next LF no longer scrolled
       the region; from below the region ESC[A went past the top margin). */
    unsigned up=p->row>=p->top?p->top:0,down=p->row<=p->bottom?p->bottom:t->rows-1;
    if(t->private_csi&&final!='h'&&final!='l'&&final!='n')return;
    switch(final){
    case 'A':p->row=p->row>up+tm_min(n,t->rows)?p->row-n:up;break;
    case 'B':case 'e':p->row=tm_min(p->row+tm_min(n,t->rows),down);break;
    case 'C':case 'a':p->col=tm_min(p->col+tm_min(n,t->cols),t->cols-1);break;
    case 'D':p->col=p->col>n?p->col-n:0;break;
    case 'E':p->row=tm_min(p->row+tm_min(n,t->rows),down);p->col=0;break;
    case 'F':p->row=p->row>up+tm_min(n,t->rows)?p->row-n:up;p->col=0;break;
    case 'G':case '`':p->col=tm_min(n-1,t->cols-1);break;
    case 'd':tm_position(t,n-1,p->col);break;
    case 'H':case 'f':tm_position(t,n-1,tm_param(t,1,1)-1);break;
    case 'J':
        if(zero==0){tm_erase(t,p->row,p->col,t->cols);for(unsigned r=p->row+1;r<t->rows;r++)tm_clear_line(t,&p->lines[r],p->style);}
        else if(zero==1){for(unsigned r=0;r<p->row;r++)tm_clear_line(t,&p->lines[r],p->style);tm_erase(t,p->row,0,p->col+1);}
        else if(zero==2)for(unsigned r=0;r<t->rows;r++)tm_clear_line(t,&p->lines[r],p->style);
        else if(zero==3&&!t->page){t->dropped_lines+=t->history_count;t->history_count=t->history_first=0;}
        break;
    case 'K':if(zero==0)tm_erase(t,p->row,p->col,t->cols);else if(zero==1)tm_erase(t,p->row,0,p->col+1);else if(zero==2)tm_erase(t,p->row,0,t->cols);break;
    case 'm':tm_sgr(t);return;
    case 's':tm_save_cursor(t);return;
    case 'u':tm_restore_cursor(t);break;
    case 'r':{unsigned first=tm_param(t,0,1),last=tm_param(t,1,t->rows);if(first<last&&last<=t->rows){p->top=first-1;p->bottom=last-1;tm_position(t,0,0);}break;}
    case 'S':tm_scroll_up(t,p->top,p->bottom,n,false);break;
    case 'T':tm_scroll_down(t,p->top,p->bottom,n);break;
    case 'L':if(p->row>=p->top&&p->row<=p->bottom)tm_scroll_down(t,p->row,p->bottom,n);break;
    case 'M':if(p->row>=p->top&&p->row<=p->bottom)tm_scroll_up(t,p->row,p->bottom,n,false);break;
    case '@':case 'P':{
        unsigned count=tm_min(n,t->cols-p->col),remaining=t->cols-p->col-count;TmLine*line=&p->lines[p->row];
        if(final=='@'){if(remaining)memmove(&line->cells[p->col+count],&line->cells[p->col],remaining*sizeof(TmCell));tm_erase(t,p->row,p->col,p->col+count);}
        else{if(remaining)memmove(&line->cells[p->col],&line->cells[p->col+count],remaining*sizeof(TmCell));tm_erase(t,p->row,t->cols-count,t->cols);}
        tm_fix_wide(t,line);break;
    }
    case 'X':tm_erase(t,p->row,p->col,p->col+tm_min(n,t->cols));break;
    case 'I':for(unsigned k=0;k<tm_min(n,t->cols);k++){do{if(p->col+1<t->cols)p->col++;else break;}while(!t->tabs[p->col]);}break;
    case 'Z':for(unsigned k=0;k<tm_min(n,t->cols);k++){do{if(p->col)p->col--;else break;}while(!t->tabs[p->col]);}break;
    case 'g':if(!zero)t->tabs[p->col]=false;else if(zero==3)memset(t->tabs,0,sizeof(t->tabs));break;
    case 'h':case 'l':tm_modes(t,final=='h');return;
    case 'n':if(zero==5&&!t->private_csi)tm_reply(t,"\033[0n");else if(zero==6){char out[48];snprintf(out,sizeof(out),"\033[%s%u;%uR",t->private_csi?"?":"",p->row-(p->origin?p->top:0)+1,p->col+1);tm_reply(t,out);}return;
    case 'c':if(!zero&&!t->private_csi)tm_reply(t,"\033[?1;0c");return;
    default:return;
    }
    tm_page(t)->wrap_pending=false;t->generation++;
}
static inline void tm_control(TerminalModel*t,unsigned char byte){
    TmPage*p=tm_page(t);
    if(byte=='\r'){p->col=0;p->wrap_pending=false;}
    else if(byte=='\n'||byte=='\v'||byte=='\f'){tm_index(t);if(t->newline)p->col=0;}
    else if(byte=='\b'){if(p->col)p->col--;p->wrap_pending=false;}
    else if(byte=='\t'){do{if(p->col+1<t->cols)p->col++;else break;}while(!t->tabs[p->col]);p->wrap_pending=false;}
    else if(byte==14)t->shift_out=true;else if(byte==15)t->shift_out=false;
    t->generation++;
}
static inline void tm_byte(TerminalModel*t,unsigned char byte){
again:
    if(t->parser==TM_OSC||t->parser==TM_STRING){if(byte==27)t->parser=TM_STRING_ESC;else if(byte==7&&t->parser==TM_OSC)t->parser=TM_GROUND;else if(byte==24||byte==26)t->parser=TM_GROUND;return;}
    /* ESC that is not ST ends the string and starts a sequence of its own, as xterm does (2026-11-14
       review: inside an OSC it switched to a string BEL no longer ended, and all later output was
       swallowed until a chance ESC \). */
    if(t->parser==TM_STRING_ESC){if(byte=='\\'){t->parser=TM_GROUND;return;}t->parser=TM_ESCAPE;goto again;}
    if(byte==24||byte==26){t->parser=TM_GROUND;t->utf_left=0;return;}
    if(byte==27){if(t->utf_left){tm_put(t,0xfffd);t->utf_left=0;}t->parser=TM_ESCAPE;return;}
    if(byte<32){if(t->utf_left){tm_put(t,0xfffd);t->utf_left=0;}tm_control(t,byte);return;}
    if(byte==127)return;
    if(t->parser==TM_CHARSET){t->graphics[t->charset]=byte=='0';t->parser=TM_GROUND;return;}
    if(t->parser==TM_ESC_IGNORE){t->parser=TM_GROUND;return;}
    if(t->parser==TM_ESCAPE){
        t->parser=TM_GROUND;
        if(byte=='['){t->parser=TM_CSI;t->param_count=1;memset(t->params,0,sizeof(t->params));t->private_csi=t->ignore_csi=t->param_present=false;t->sequence_bytes=0;}
        else if(byte==']')t->parser=TM_OSC;else if(byte=='P'||byte=='_'||byte=='^'||byte=='X')t->parser=TM_STRING;
        else if(byte=='('||byte==')'){t->parser=TM_CHARSET;t->charset=byte==')';}
        else if(byte=='#'||byte=='%'||byte==' ')t->parser=TM_ESC_IGNORE;
        else if(byte=='7')tm_save_cursor(t);else if(byte=='8')tm_restore_cursor(t);
        else if(byte=='D')tm_index(t);else if(byte=='M')tm_reverse_index(t);else if(byte=='E'){tm_page(t)->col=0;tm_index(t);}
        else if(byte=='H')t->tabs[tm_page(t)->col]=true;
        else if(byte=='c'){unsigned rows=t->rows,cols=t->cols;tm_init(t,rows,cols);}
        else if(byte=='Z')tm_reply(t,"\033[?1;0c");
        t->generation++;return;
    }
    if(t->parser==TM_CSI){
        if(++t->sequence_bytes>512)t->ignore_csi=true;
        if(byte>='0'&&byte<='9'){unsigned*i=&t->params[t->param_count-1];*i=*i<10000?tm_min(*i*10u+byte-'0',10000):10000;t->param_present=true;return;}
        if(byte==';'){if(t->param_count<TM_PARAMS)t->param_count++;else t->ignore_csi=true;return;}
        if(byte=='?'&&t->param_count==1&&!t->param_present){t->private_csi=true;return;}
        if(byte>=0x40&&byte<=0x7e){tm_csi(t,byte);t->parser=TM_GROUND;return;}
        if(byte>=0x20&&byte<=0x3f){t->ignore_csi=true;return;}
        t->parser=TM_GROUND;return;
    }
    if(t->utf_left){
        if((byte&0xc0)!=0x80){t->utf_left=0;tm_put(t,0xfffd);goto again;}
        t->utf_value=(t->utf_value<<6)|(byte&63u);
        if(!--t->utf_left){uint32_t cp=t->utf_value;if(cp<t->utf_min||cp>0x10ffff||(cp>=0xd800&&cp<=0xdfff))cp=0xfffd;tm_put(t,cp);}return;
    }
    if(byte<0x80){tm_put(t,byte);return;}
    if(byte>=0xc2&&byte<=0xdf){t->utf_value=byte&31u;t->utf_left=1;t->utf_min=0x80;}
    else if(byte>=0xe0&&byte<=0xef){t->utf_value=byte&15u;t->utf_left=2;t->utf_min=0x800;}
    else if(byte>=0xf0&&byte<=0xf4){t->utf_value=byte&7u;t->utf_left=3;t->utf_min=0x10000;}
    else tm_put(t,0xfffd);
}
static inline void tm_feed(TerminalModel*t,const void*data,size_t length){const unsigned char*p=data;for(size_t n=0;n<length;n++)tm_byte(t,p[n]);}

/* Strict canonical base64; no decoding to C strings, so NUL/control bytes are
 * retained as data. The caller supplies the exact fixed input/output bounds. */
static inline size_t tm_base64_encode(const unsigned char*data,size_t length,char*out,size_t capacity){
    static const char table[]="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    size_t need=((length+2)/3)*4;if(need+1>capacity)return SIZE_MAX;size_t at=0;
    for(size_t n=0;n<length;n+=3){unsigned x=(unsigned)data[n]<<16;if(n+1<length)x|=(unsigned)data[n+1]<<8;if(n+2<length)x|=data[n+2];out[at++]=table[(x>>18)&63];out[at++]=table[(x>>12)&63];out[at++]=n+1<length?table[(x>>6)&63]:'=';out[at++]=n+2<length?table[x&63]:'=';}
    out[at]=0;return at;
}
static inline int tm_base64_digit(unsigned char c){if(c>='A'&&c<='Z')return c-'A';if(c>='a'&&c<='z')return c-'a'+26;if(c>='0'&&c<='9')return c-'0'+52;if(c=='+')return 62;if(c=='/')return 63;return -1;}
static inline size_t tm_base64_decode(const char*s,unsigned char*out,size_t capacity){
    size_t n=strlen(s),at=0;if(n%4)return SIZE_MAX;
    for(size_t i=0;i<n;i+=4){int a=tm_base64_digit((unsigned char)s[i]),b=tm_base64_digit((unsigned char)s[i+1]);int c=s[i+2]=='='?-2:tm_base64_digit((unsigned char)s[i+2]),d=s[i+3]=='='?-2:tm_base64_digit((unsigned char)s[i+3]);
        if(a<0||b<0||c==-1||d==-1||(c==-2&&d!=-2)||((c==-2||d==-2)&&i+4!=n)||(c==-2&&(b&15))||(d==-2&&c>=0&&(c&3)))return SIZE_MAX;
        size_t count=c==-2?1:(d==-2?2:3);if(count>capacity-at)return SIZE_MAX;out[at++]=(unsigned char)((a<<2)|(b>>4));if(count>1)out[at++]=(unsigned char)((b<<4)|(c>>2));if(count>2)out[at++]=(unsigned char)((c<<6)|d);
    }
    return at;
}
#endif
