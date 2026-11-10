/* SPDX-License-Identifier: MIT */
#include "json_min.h"
#include <ctype.h>
#include <errno.h>
#include <stdlib.h>
#include <string.h>

typedef struct { Json *j; int pos,depth; } Parser;
static void whitespace(Parser *p){while(isspace((unsigned char)p->j->text[p->pos]))p->pos++;}
static int value(Parser *p,int parent);
static int add(Parser *p,int type,int parent){
    Json *j=p->j;
    if(j->count>=j->capacity)return -1;
    int i=j->count++;j->tokens[i]=(JsonToken){type,p->pos,-1,parent};return i;
}
static int string(Parser *p,int parent){
    int i=add(p,J_STRING,parent);if(i<0)return -1;
    p->j->tokens[i].start=++p->pos;
    const char *s=p->j->text;
    while(s[p->pos]&&s[p->pos]!='"'){
        if((unsigned char)s[p->pos]<32)return -1;
        if(s[p->pos]=='\\'){
            char e=s[++p->pos];
            if(!e)return -1;
            if(e=='u'){
                for(int n=0;n<4;n++)if(!isxdigit((unsigned char)s[++p->pos]))return -1;
            }else if(!strchr("\"\\/bfnrt",e))return -1;
        }
        p->pos++;
    }
    if(s[p->pos]!='"')return -1;
    p->j->tokens[i].end=p->pos++;return i;
}
static int number_valid(const char *s,int n){
    int i=0;if(i<n&&s[i]=='-')i++;
    if(i>=n)return 0;
    if(s[i]=='0')i++;
    else {if(s[i]<'1'||s[i]>'9')return 0;while(i<n&&isdigit((unsigned char)s[i]))i++;}
    if(i<n&&s[i]=='.'){i++;int a=i;while(i<n&&isdigit((unsigned char)s[i]))i++;if(a==i)return 0;}
    if(i<n&&(s[i]=='e'||s[i]=='E')){i++;if(i<n&&(s[i]=='+'||s[i]=='-'))i++;int a=i;while(i<n&&isdigit((unsigned char)s[i]))i++;if(a==i)return 0;}
    return i==n;
}
static int value(Parser *p,int parent){
    whitespace(p);if(++p->depth>32)return -1;
    const char *s=p->j->text;int i;
    if(s[p->pos]=='"')i=string(p,parent);
    else if(s[p->pos]=='{'||s[p->pos]=='['){
        int object=s[p->pos]=='{';char end=object?'}':']';
        i=add(p,object?J_OBJECT:J_ARRAY,parent);if(i<0)return -1;p->pos++;whitespace(p);
        if(s[p->pos]!=end)for(;;){
            if(object){if(s[p->pos]!='"'||string(p,i)<0)return -1;whitespace(p);if(s[p->pos++]!=':')return -1;}
            if(value(p,i)<0)return -1;
            whitespace(p);
            if(s[p->pos]==end)break;
            if(s[p->pos++]!=',')return -1;
            whitespace(p);
        }
        p->j->tokens[i].end=++p->pos;
    }else{
        i=add(p,J_PRIMITIVE,parent);if(i<0)return -1;int start=p->pos;
        while(s[p->pos]&&!isspace((unsigned char)s[p->pos])&&!strchr(",]}",s[p->pos]))p->pos++;
        int n=p->pos-start;
        if(!((n==4&&!memcmp(s+start,"true",4))||(n==5&&!memcmp(s+start,"false",5))||(n==4&&!memcmp(s+start,"null",4))||number_valid(s+start,n)))return -1;
        p->j->tokens[i].end=p->pos;
    }
    p->depth--;return i;
}
int json_parse(Json *j,const char *s,JsonToken *t,int n){
    *j=(Json){s,t,0,n};Parser p={j,0,0};if(value(&p,-1)!=0)return -1;whitespace(&p);return s[p.pos]? -1:j->count;
}
int json_next(const Json *j,int i){int end=j->tokens[i].end;i++;while(i<j->count&&j->tokens[i].start<end)i++;return i;}
int json_get(const Json *j,int o,const char *key){
    if(o<0||o>=j->count||j->tokens[o].type!=J_OBJECT)return -1;
    for(int i=o+1;i<j->count&&j->tokens[i].start<j->tokens[o].end;){
        int v=i+1;size_t n=(size_t)(j->tokens[i].end-j->tokens[i].start);
        if(j->tokens[i].type!=J_STRING||v>=j->count)return -1;
        if(strlen(key)==n&&!memcmp(j->text+j->tokens[i].start,key,n))return v;
        i=json_next(j,v);
    }
    return -1;
}
static unsigned hex4(const char*s){
    unsigned n=0;
    for(int i=0;i<4;i++){unsigned char c=(unsigned char)s[i];n=(n<<4)|(unsigned)(c<='9'?c-'0':(tolower(c)-'a'+10));}
    return n;
}
int json_string(const Json *j,int t,char *out,size_t size){
    if(!size)return -1;
    out[0]=0;
    if(t<0||t>=j->count||j->tokens[t].type!=J_STRING)return -1;
    size_t pos=0;const char *s=j->text;
    for(int i=j->tokens[t].start;i<j->tokens[t].end;i++){
        unsigned char c=(unsigned char)s[i];char bytes[4];size_t count=1;bytes[0]=(char)c;
        if(c=='\\'){
            c=(unsigned char)s[++i];
            if(c=='n')bytes[0]='\n';else if(c=='r')bytes[0]='\r';else if(c=='t')bytes[0]='\t';else if(c=='b')bytes[0]='\b';else if(c=='f')bytes[0]='\f';
            else if(c=='u'){
                unsigned u=hex4(s+i+1);i+=4;
                if(u>=0xd800&&u<=0xdbff){
                    if(i+6>=j->tokens[t].end||s[i+1]!='\\'||s[i+2]!='u'){out[0]=0;return -1;}
                    unsigned low=hex4(s+i+3);if(low<0xdc00||low>0xdfff){out[0]=0;return -1;}
                    u=0x10000+((u-0xd800)<<10)+(low-0xdc00);i+=6;
                }else if(u>=0xdc00&&u<=0xdfff){out[0]=0;return -1;}
                if(u==0){out[0]=0;return -1;}
                if(u<0x80)bytes[0]=(char)u;
                else if(u<0x800){count=2;bytes[0]=(char)(0xc0|(u>>6));bytes[1]=(char)(0x80|(u&63));}
                else if(u<0x10000){count=3;bytes[0]=(char)(0xe0|(u>>12));bytes[1]=(char)(0x80|((u>>6)&63));bytes[2]=(char)(0x80|(u&63));}
                else{count=4;bytes[0]=(char)(0xf0|(u>>18));bytes[1]=(char)(0x80|((u>>12)&63));bytes[2]=(char)(0x80|((u>>6)&63));bytes[3]=(char)(0x80|(u&63));}
            }else bytes[0]=(char)c;
        }
        if(pos+count>=size){out[0]=0;return -1;}memcpy(out+pos,bytes,count);pos+=count;
    }
    out[pos]=0;return (int)pos;
}
int json_bool(const Json *j,int t,int f){
    if(t<0||t>=j->count||j->tokens[t].type!=J_PRIMITIVE)return f;
    int n=j->tokens[t].end-j->tokens[t].start;const char*s=j->text+j->tokens[t].start;
    if(n==4&&!memcmp(s,"true",4))return 1;
    if(n==5&&!memcmp(s,"false",5))return 0;
    return f;
}
uint64_t json_uint(const Json*j,int t,uint64_t f){
    if(t<0||t>=j->count||j->tokens[t].type!=J_PRIMITIVE)return f;
    int n=j->tokens[t].end-j->tokens[t].start;
    if(n<=0||n>20)return f;
    char b[24];memcpy(b,j->text+j->tokens[t].start,(size_t)n);b[n]=0;
    for(int i=0;i<n;i++)if(!isdigit((unsigned char)b[i]))return f;
    errno=0;char*e;unsigned long long v=strtoull(b,&e,10);return errno||*e?f:(uint64_t)v;
}
