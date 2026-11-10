/* SPDX-License-Identifier: MIT — pure ANSI/UTF-8 grid tests, no PTY/device I/O. */
#include <stdlib.h>
#include <stdio.h>
#include "terminal_model.h"

#define CHECK(x) do{checks++;if(!(x)){fprintf(stderr,"terminal-model check %u line %d failed: %s\n",checks,__LINE__,#x);free(t);return 1;}}while(0)
static void feed(TerminalModel*t,const char*s){tm_feed(t,s,strlen(s));}
static uint32_t at(TerminalModel*t,unsigned row,unsigned col){return tm_page(t)->lines[row].cells[col].cp;}
static bool row_is(TerminalModel*t,unsigned row,const char*s){for(unsigned c=0;s[c];c++)if(at(t,row,c)!=(unsigned char)s[c])return false;return true;}
int main(void){
    unsigned checks=0;TerminalModel*t=calloc(1,sizeof(*t));if(!t)return 2;
    CHECK(tm_init(t,4,12));const char*uk="A Ґіїє €";
    for(size_t n=0;n<strlen(uk);n++)tm_feed(t,uk+n,1);
    CHECK(at(t,0,2)==0x490&&at(t,0,3)==0x456&&at(t,0,4)==0x457&&at(t,0,5)==0x454&&at(t,0,7)==0x20ac&&tm_page(t)->col==8);
    feed(t,"\rX");CHECK(row_is(t,0,"X "));feed(t,"\bZ");CHECK(at(t,0,0)=='Z');
    feed(t,"\r\tT");CHECK(at(t,0,8)=='T'&&tm_page(t)->col==9);
    feed(t,"\nN");CHECK(at(t,1,9)=='N');feed(t,"\rR");CHECK(at(t,1,0)=='R');

    CHECK(tm_init(t,2,4));feed(t,"abcd");CHECK(t->history_count==0&&tm_page(t)->row==0&&tm_page(t)->col==3&&tm_page(t)->wrap_pending);
    feed(t,"e");CHECK(row_is(t,0,"abcd")&&at(t,1,0)=='e'&&!tm_page(t)->wrap_pending);
    feed(t,"fghI");CHECK(t->history_count==1&&tm_line(t,0)->cells[0].cp=='a'&&row_is(t,0,"efgh")&&at(t,1,0)=='I');
    feed(t,"\033[?7l\033[1;4HXyz");CHECK(at(t,0,3)=='z'&&tm_page(t)->col==3&&!tm_page(t)->wrap_pending);

    CHECK(tm_init(t,5,12));feed(t,"\033[4;7HX\033[2A\033[3DY");CHECK(at(t,3,6)=='X'&&at(t,1,4)=='Y');
    feed(t,"\033[2;2H\0337\033[5;10H!\0338Q");CHECK(at(t,1,1)=='Q');
    feed(t,"\033[3;1Habcdefghij\033[3;4H\033[K");CHECK(row_is(t,2,"abc         "));
    feed(t,"\033[3;5H\033[1K");CHECK(at(t,2,0)==' '&&at(t,2,4)==' ');
    feed(t,"\033[2J");CHECK(tm_page(t)->row==2&&tm_page(t)->col==4&&at(t,3,6)==' ');
    feed(t,"\033[H\033[1;31;44mR\033[0mD");TmCell cell=tm_page(t)->lines[0].cells[0];
    CHECK(cell.cp=='R'&&cell.fg==tm_color(1)&&cell.bg==tm_color(4)&&(cell.flags&TM_BOLD));
    CHECK(tm_page(t)->lines[0].cells[1].fg==TM_DEFAULT_FG&&tm_page(t)->lines[0].cells[1].flags==0);
    feed(t,"\033[38;5;196;48;2;1;2;3;4;7mC");cell=tm_page(t)->lines[0].cells[2];
    CHECK(cell.fg==0xff0000&&cell.bg==0x010203&&(cell.flags&(TM_UNDERLINE|TM_INVERSE))==(TM_UNDERLINE|TM_INVERSE));
    feed(t,"\033[2;22;24;27mD");CHECK(tm_page(t)->lines[0].cells[3].flags==0);

    CHECK(tm_init(t,5,8));feed(t,"11111111\r\n22222222\r\n33333333\r\n44444444\r\n55555555");
    feed(t,"\033[2;4r\033[4;1H\n");CHECK(row_is(t,0,"11111111")&&row_is(t,1,"33333333")&&row_is(t,2,"44444444")&&row_is(t,4,"55555555")&&t->history_count==0);
    feed(t,"\033[?6h\033[99;99H");CHECK(tm_page(t)->row==3&&tm_page(t)->col==7);
    feed(t,"\033[H\033M");CHECK(row_is(t,0,"11111111")&&at(t,1,0)==' '&&row_is(t,2,"33333333"));
    feed(t,"\033[?6l\033[1;5r\033[2;1H\033[L");CHECK(at(t,1,0)==' '&&at(t,2,0)==' '&&row_is(t,3,"33333333"));
    feed(t,"\033[M");CHECK(row_is(t,2,"33333333"));
    feed(t,"\033[1;1Habcdefgh\033[1;3H\033[2@XY");CHECK(row_is(t,0,"abXYcdef"));
    feed(t,"\033[1;3H\033[2P");CHECK(row_is(t,0,"abcdef  "));
    feed(t,"\033[2X");CHECK(row_is(t,0,"ab  ef  "));

    CHECK(tm_init(t,3,12));feed(t,"main\033[2;4H\033[?1049hALT\033[?25l");CHECK(t->page==1&&row_is(t,0,"ALT")&&!t->cursor_visible&&tm_line_count(t)==3);
    feed(t,"\033[?1049l\033[?25h!");CHECK(t->page==0&&row_is(t,0,"main")&&at(t,1,3)=='!'&&t->cursor_visible);
    feed(t,"\033[?1h");CHECK(t->application_cursor);feed(t,"\033[?1l");CHECK(!t->application_cursor);
    feed(t,"\033[2;3H\033[6n\033[5n");CHECK(t->reply_length==10&&!memcmp(t->replies,"\033[2;3R\033[0n",10));
    for(unsigned n=0;n<100;n++){feed(t,"\033[6n");}
    CHECK(t->reply_length<=sizeof(t->replies));

    CHECK(tm_init(t,3,20));feed(t,"a\033]52;c;SECRET\007b\033Pignored\033\\c\033]0;title\033\\d");CHECK(row_is(t,0,"abcd")&&tm_page(t)->col==4);
    feed(t,"\033(0lqk\033(B");CHECK(at(t,0,4)==0x250c&&at(t,0,5)==0x2500&&at(t,0,6)==0x2510);
    feed(t,"\033[1;");CHECK(t->parser==TM_CSI);feed(t,"2HZ");CHECK(at(t,0,1)=='Z');
    feed(t,"\033[99999999999999999999999999999999999;99999999999999999999999999H@");CHECK(at(t,2,19)=='@');
    feed(t,"\033[1;1H\033[");for(unsigned n=0;n<100;n++)feed(t,"1;");feed(t,"mX");CHECK(at(t,0,0)=='X'&&t->param_count<=TM_PARAMS);
    feed(t,"\033[12\030Y");CHECK(at(t,0,1)=='Y'&&t->parser==TM_GROUND);

    CHECK(tm_init(t,3,12));unsigned char malformed[]={0xc0,0xaf,0xe2,'A',0xed,0xa0,0x80,0xf4,0x90,0x80,0x80};tm_feed(t,malformed,sizeof(malformed));
    CHECK(at(t,0,0)==0xfffd&&at(t,0,1)==0xfffd&&at(t,0,2)==0xfffd&&at(t,0,3)=='A'&&at(t,0,4)==0xfffd&&at(t,0,5)==0xfffd);
    feed(t,"\033[2;1He\xcc\x81\xe4\xb8\xadX");CHECK(at(t,1,0)=='e'&&tm_page(t)->lines[1].cells[0].mark==0x301&&at(t,1,1)==0x4e2d&&tm_page(t)->lines[1].cells[2].width==0&&at(t,1,3)=='X');
    feed(t,"\033[2;3HZ");CHECK(at(t,1,1)==' '&&at(t,1,2)=='Z');

    CHECK(tm_init(t,3,16));for(unsigned n=0;n<700;n++){char line[32];snprintf(line,sizeof(line),"%04u\r\n",n);feed(t,line);}
    CHECK(t->history_count==TM_HISTORY&&t->dropped_lines==186&&tm_line_count(t)==515&&row_is(t,0,"0698")&&row_is(t,1,"0699"));
    CHECK(tm_line(t,0)->cells[0].cp=='0'&&tm_line(t,0)->cells[1].cp=='1'&&tm_line(t,0)->cells[2].cp=='8'&&tm_line(t,0)->cells[3].cp=='6');
    feed(t,"\033[3J");CHECK(t->history_count==0&&t->dropped_lines==698);

    unsigned char all[256],decoded[256];char encoded[345];for(unsigned n=0;n<256;n++)all[n]=(unsigned char)n;
    CHECK(tm_base64_encode(all,sizeof(all),encoded,sizeof(encoded))==344&&tm_base64_decode(encoded,decoded,sizeof(decoded))==256&&!memcmp(all,decoded,256));
    CHECK(tm_base64_decode("AAEC",decoded,2)==SIZE_MAX&&tm_base64_decode("A===",decoded,256)==SIZE_MAX&&tm_base64_decode("AB==",decoded,256)==SIZE_MAX&&tm_base64_decode("AA=A",decoded,256)==SIZE_MAX&&tm_base64_decode("AAA",decoded,256)==SIZE_MAX&&tm_base64_decode("AA==AAAA",decoded,256)==SIZE_MAX);
    CHECK(tm_base64_decode("",decoded,0)==0&&tm_base64_decode("AA==",decoded,1)==1&&decoded[0]==0);

    CHECK(tm_init(t,60,80));uint32_t random=0x12345678;unsigned char bytes[127];
    for(unsigned chunk=0;chunk<10000;chunk++){
        for(unsigned n=0;n<sizeof(bytes);n++){random=random*1664525u+1013904223u;bytes[n]=(unsigned char)(random>>24);}tm_feed(t,bytes,sizeof(bytes));
        if(tm_page(t)->row>=t->rows||tm_page(t)->col>=t->cols||tm_page(t)->top>tm_page(t)->bottom||tm_page(t)->bottom>=t->rows||t->history_count>TM_HISTORY||t->history_first>=TM_HISTORY||t->param_count>TM_PARAMS){fprintf(stderr,"terminal-model bounds failed at chunk %u\n",chunk);free(t);return 1;}
    }
    CHECK(t->rows==60&&t->cols==80);
    free(t);printf("{\"ok\":true,\"checks\":%u,\"fuzz_bytes\":1270000,\"device_io\":false,\"pty_io\":false}\n",checks);return 0;
}
