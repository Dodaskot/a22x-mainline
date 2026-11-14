/* SPDX-License-Identifier: MIT
 * Software canvas at panel resolution. Shapes take their coverage from the exact
 * distance to their edge, so curves and diagonals are smooth without oversampling
 * and nothing is stretched after drawing.
 */
#ifndef A22X_NATIVE_CANVAS_H
#define A22X_NATIVE_CANVAS_H

#include <math.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include "theme_assets.h"

#define NW 1080
#define NH 2400

typedef struct { int x0,y0,x1,y1; } Bounds;

static inline uint32_t canvas_blend(uint32_t under,uint32_t color,unsigned int alpha){
    unsigned int over=alpha+(alpha>>7),rest=256u-over;
    return ((((color&0xff00ffu)*over+(under&0xff00ffu)*rest)>>8)&0xff00ffu)|
           ((((color&0x00ff00u)*over+(under&0x00ff00u)*rest)>>8)&0x00ff00u);
}
static inline uint32_t canvas_mix(uint32_t from,uint32_t to,float amount){
    return canvas_blend(from,to,(unsigned int)(amount<=0?0:(amount>=1?255:amount*255.f+.5f)));
}
static inline float canvas_cover(float distance){float value=.5f-distance;return value<=0?0:(value>=1?1:value);}
/* The same arithmetic as canvas_blend with the colour side worked out once per run, in a
 * form the compiler can turn into vector code: this loop paints most of every frame. */
static inline void canvas_span(uint32_t*restrict row,int x0,int x1,uint32_t color,unsigned int alpha){
    if(alpha>=255)for(int x=x0;x<x1;x++)row[x]=color;
    else if(alpha){
        const uint32_t over=alpha+(alpha>>7),rest=256u-over,red_blue=(color&0xff00ffu)*over,green=(color&0x00ff00u)*over;
        for(int x=x0;x<x1;x++){
            uint32_t under=row[x];
            row[x]=(((red_blue+(under&0xff00ffu)*rest)>>8)&0xff00ffu)|(((green+(under&0x00ff00u)*rest)>>8)&0x00ff00u);
        }
    }
}
static void canvas_rect(uint32_t*pixels,Bounds b,int x0,int y0,int x1,int y1,uint32_t color,unsigned int alpha){
    if(x0<b.x0)x0=b.x0;
    if(y0<b.y0)y0=b.y0;
    if(x1>b.x1)x1=b.x1;
    if(y1>b.y1)y1=b.y1;
    if(x1<=x0)return;
    for(int y=y0;y<y1;y++)canvas_span(pixels+(size_t)y*NW,x0,x1,color,alpha);
}
/* An RGBA picture (straight alpha) at panel resolution, such as an app's own icon (2026-11-09). */
static void canvas_rgba(uint32_t*pixels,Bounds b,int x0,int y0,const uint8_t*rgba,int w,int h){
    for(int y=0;y<h;y++){
        int py=y0+y;if(py<b.y0||py>=b.y1)continue;
        uint32_t*row=pixels+(size_t)py*NW;const uint8_t*s=rgba+(size_t)y*(size_t)w*4u;
        for(int x=0;x<w;x++,s+=4){
            int px=x0+x;if(px<b.x0||px>=b.x1||!s[3])continue;
            uint32_t color=(uint32_t)s[0]<<16|(uint32_t)s[1]<<8|s[2];
            row[px]=s[3]==255?color:canvas_blend(row[px],color,s[3]);
        }
    }
}
/* Corner coverage depends only on the radius, so it is worked out once per radius. Along a row
 * coverage only grows towards the card: a few soft pixels, then solid fill for a vector span. */
typedef struct { float radius;int corner;float*cover;uint16_t*start,*full; } CanvasCorner;
static const CanvasCorner*canvas_corner(float radius,int corner){
    static CanvasCorner cache[32];static int count;
    if(corner<=0||corner>160)return NULL;
    for(int n=0;n<count;n++)if(cache[n].radius==radius&&cache[n].corner==corner)return&cache[n];
    if(count==32)return NULL;
    float*cover=malloc((size_t)corner*(size_t)corner*sizeof(float));uint16_t*start=malloc((size_t)corner*sizeof(uint16_t)),*full=malloc((size_t)corner*sizeof(uint16_t));
    if(!cover||!start||!full){free(cover);free(start);free(full);return NULL;}
    for(int edge=0;edge<corner;edge++){
        float dy=radius-((float)edge+.5f);
        if(dy<0)dy=0;
        int first=corner,solid=corner;
        for(int column=corner-1;column>=0;column--){
            float dx=radius-((float)column+.5f);
            if(dx<0)dx=0;
            float value=canvas_cover(sqrtf(dx*dx+dy*dy)-radius);cover[(size_t)edge*(size_t)corner+(size_t)column]=value;
            if(value>0)first=column;
            if(value>=1&&solid==column+1)solid=column;
        }
        start[edge]=(uint16_t)first;full[edge]=(uint16_t)solid;
    }
    cache[count]=(CanvasCorner){radius,corner,cover,start,full};return&cache[count++];
}
/* first and last are the top and bottom colors; equal values give a flat fill. */
static void canvas_rounded(uint32_t*pixels,Bounds b,int left,int top,int right,int bottom,float radius,uint32_t first,uint32_t last,unsigned int alpha){
    int width=right-left,height=bottom-top;
    if(width<=0||height<=0)return;
    float limit=(float)(width<height?width:height)*.5f;
    /* A radius cut down to a shape's size (a growing progress fill, a slider) changes from frame to
       frame: it is not kept, or it filled the 32 places for good (2026-11-14 review). */
    bool cut=radius>limit;
    if(cut)radius=limit;
    if(radius<0)radius=0;
    int corner=(int)ceilf(radius);
    int x0=left>b.x0?left:b.x0,x1=right<b.x1?right:b.x1,y0=top>b.y0?top:b.y0,y1=bottom<b.y1?bottom:b.y1;
    if(x1<=x0)return;
    int inner0=left+corner>x0?left+corner:x0,inner1=right-corner<x1?right-corner:x1;
    const CanvasCorner*table=!cut&&width>=2*corner?canvas_corner(radius,corner):NULL;
    for(int y=y0;y<y1;y++){
        uint32_t*row=pixels+(size_t)y*NW;
        uint32_t color=first==last?first:canvas_mix(first,last,((float)(y-top)+.5f)/(float)height);
        int from_edge=y-top<bottom-1-y?y-top:bottom-1-y;
        if(from_edge>=corner){canvas_span(row,x0,x1,color,alpha);continue;}
        if(table){
            const float*cover=table->cover+(size_t)from_edge*(size_t)corner;int solid=table->full[from_edge];
            for(int column=table->start[from_edge];column<solid;column++){
                unsigned int value=(unsigned int)(cover[column]*(float)alpha+.5f);
                if(!value)continue;
                int l=left+column,r=right-1-column;
                if(l>=x0&&l<x1)row[l]=canvas_blend(row[l],color,value);
                if(r>=x0&&r<x1)row[r]=canvas_blend(row[r],color,value);
            }
            int from=left+solid>x0?left+solid:x0,to=right-solid<x1?right-solid:x1;
            if(to>from)canvas_span(row,from,to,color,alpha);
            continue;
        }
        float dy=radius-((float)from_edge+.5f);
        if(dy<0)dy=0;
        for(int column=0;column<corner;column++){
            float dx=radius-((float)column+.5f);
            if(dx<0)dx=0;
            unsigned int value=(unsigned int)(canvas_cover(sqrtf(dx*dx+dy*dy)-radius)*(float)alpha+.5f);
            if(!value)continue;
            int l=left+column,r=right-1-column;
            if(l>=x0&&l<x1)row[l]=canvas_blend(row[l],color,value);
            if(r!=l&&r>=x0&&r<x1)row[r]=canvas_blend(row[r],color,value);
        }
        if(inner1>inner0)canvas_span(row,inner0,inner1,color,alpha);
    }
}
static void canvas_capsule(uint32_t*pixels,Bounds b,float ax,float ay,float bx,float by,float width,uint32_t color){
    float radius=width*.5f,dx=bx-ax,dy=by-ay,length=dx*dx+dy*dy,inverse=length>0?1.f/length:0;
    int x0=(int)floorf((ax<bx?ax:bx)-radius)-1,y0=(int)floorf((ay<by?ay:by)-radius)-1;
    int x1=(int)ceilf((ax>bx?ax:bx)+radius)+2,y1=(int)ceilf((ay>by?ay:by)+radius)+2;
    if(x0<b.x0)x0=b.x0;
    if(y0<b.y0)y0=b.y0;
    if(x1>b.x1)x1=b.x1;
    if(y1>b.y1)y1=b.y1;
    for(int y=y0;y<y1;y++){
        uint32_t*row=pixels+(size_t)y*NW;float py=(float)y+.5f;
        for(int x=x0;x<x1;x++){
            float px=(float)x+.5f,t=((px-ax)*dx+(py-ay)*dy)*inverse;
            t=t<0?0:(t>1?1:t);
            float ex=px-(ax+t*dx),ey=py-(ay+t*dy);
            unsigned int value=(unsigned int)(canvas_cover(sqrtf(ex*ex+ey*ey)-radius)*255.f+.5f);
            if(value)row[x]=canvas_blend(row[x],color,value);
        }
    }
}
/* A ring keeps width pixels inside radius; width at or above radius is a disc. */
static void canvas_ring(uint32_t*pixels,Bounds b,float cx,float cy,float radius,float width,uint32_t color){
    int x0=(int)floorf(cx-radius)-1,y0=(int)floorf(cy-radius)-1,x1=(int)ceilf(cx+radius)+2,y1=(int)ceilf(cy+radius)+2;
    float inner=radius-width;
    if(x0<b.x0)x0=b.x0;
    if(y0<b.y0)y0=b.y0;
    if(x1>b.x1)x1=b.x1;
    if(y1>b.y1)y1=b.y1;
    for(int y=y0;y<y1;y++){
        uint32_t*row=pixels+(size_t)y*NW;float dy=(float)y+.5f-cy;
        for(int x=x0;x<x1;x++){
            float dx=(float)x+.5f-cx,distance=sqrtf(dx*dx+dy*dy),value=canvas_cover(distance-radius);
            if(inner>0)value*=canvas_cover(inner-distance);
            unsigned int alpha=(unsigned int)(value*255.f+.5f);
            if(alpha)row[x]=canvas_blend(row[x],color,alpha);
        }
    }
}
/* An arc of a ring from START radians, SWEEP radians clockwise on screen, with round ends
 * (2026-11-11: the ring of the work page). */
static void canvas_arc(uint32_t*pixels,Bounds b,float cx,float cy,float radius,float width,float start,float sweep,uint32_t color){
    const float turn=6.28318531f;
    if(sweep<=0.f)return;
    if(sweep>=turn){canvas_ring(pixels,b,cx,cy,radius,width,color);return;}
    float inner=radius-width;
    int x0=(int)floorf(cx-radius)-1,y0=(int)floorf(cy-radius)-1,x1=(int)ceilf(cx+radius)+2,y1=(int)ceilf(cy+radius)+2;
    if(x0<b.x0)x0=b.x0;
    if(y0<b.y0)y0=b.y0;
    if(x1>b.x1)x1=b.x1;
    if(y1>b.y1)y1=b.y1;
    for(int y=y0;y<y1;y++){
        uint32_t*row=pixels+(size_t)y*NW;float dy=(float)y+.5f-cy;
        for(int x=x0;x<x1;x++){
            float dx=(float)x+.5f-cx,distance=sqrtf(dx*dx+dy*dy);
            if(distance<inner-1.f||distance>radius+1.f)continue;
            float angle=atan2f(dy,dx)-start;
            while(angle<0.f)angle+=turn;
            while(angle>=turn)angle-=turn;
            if(angle>sweep)continue;
            unsigned int alpha=(unsigned int)(canvas_cover(distance-radius)*canvas_cover(inner-distance)*255.f+.5f);
            if(alpha)row[x]=canvas_blend(row[x],color,alpha);
        }
    }
    float middle=radius-width*.5f,cap=width*.5f;
    canvas_ring(pixels,b,cx+cosf(start)*middle,cy+sinf(start)*middle,cap,cap,color);
    canvas_ring(pixels,b,cx+cosf(start+sweep)*middle,cy+sinf(start+sweep)*middle,cap,cap,color);
}
/* How much of a shadow pixel shows: none under the card, and a smooth share along its
 * curved corners, so a translucent card and its shadow meet with no gap. */
static inline unsigned int canvas_shadow_outside(int x,int y,int left,int top,int right,int bottom,float radius,unsigned int value){
    if(x<left||x>=right||y<top||y>=bottom)return value;
    float px=(float)x+.5f,py=(float)y+.5f,dx=px<(float)left+radius?(float)left+radius-px:(px>(float)right-radius?px-((float)right-radius):0);
    float dy=py<(float)top+radius?(float)top+radius-py:(py>(float)bottom-radius?py-((float)bottom-radius):0);
    if(dx<=0||dy<=0)return 0;
    return (unsigned int)((float)value*(1.f-canvas_cover(sqrtf(dx*dx+dy*dy)-radius)));
}
/* A round button's shadow. The tables below are cut for card corners, so a circle would
 * stand on a rounded square. Only the ring outside the button is touched. */
static void canvas_disc_shadow(uint32_t*pixels,Bounds b,int cx,int cy,float radius,float spread,int drop,uint32_t color,unsigned int strength){
    float reach=radius+spread;int span=(int)ceilf(reach),middle=cy+drop;
    for(int y=middle-span;y<=middle+span;y++){
        if(y<b.y0||y>=b.y1)continue;
        uint32_t*row=pixels+(size_t)y*NW;float sy=(float)y+.5f-(float)middle,oy=(float)y+.5f-(float)cy;
        if(reach*reach<=sy*sy)continue;
        int half=(int)ceilf(sqrtf(reach*reach-sy*sy));
        for(int x=cx-half;x<cx+half;x++){
            if(x<b.x0||x>=b.x1)continue;
            float px=(float)x+.5f-(float)cx,own=sqrtf(px*px+oy*oy)-radius;
            if(own<=-1.f)continue;
            float t=(sqrtf(px*px+sy*sy)-radius)/spread;if(t>=1.f)continue;
            if(t<0)t=0;
            float fade=(1.f-t)*(1.f-t);unsigned int shown=(unsigned int)((float)strength*fade*(1.f-canvas_cover(own))+.5f);
            if(shown)row[x]=canvas_blend(row[x],color,shown);
        }
    }
}
/* The soft shadow comes from LVGL tables: corners, plus one profile for each edge. */
static void canvas_shadow(uint32_t*pixels,Bounds b,int left,int top,int right,int bottom,float radius,uint32_t color,unsigned int strength){
    const int size=SHADOW_CORNER,x0=left-SHADOW_MARGIN,x1=right+SHADOW_MARGIN,y0=top-SHADOW_MARGIN,y1=bottom+SHADOW_MARGIN,middle_x=(left+right)/2;
    float limit=(float)((right-left)<(bottom-top)?right-left:bottom-top)*.5f;
    if(radius>limit)radius=limit;
    for(int y=y0>b.y0?y0:b.y0;y<y1&&y<b.y1;y++){
        uint32_t*row=pixels+(size_t)y*NW;int from_top=y-y0,from_bottom=y1-1-y,columns=size,edge=0;
        const uint8_t*upper=from_top<size?shadow_top_left+(size_t)from_top*(size_t)size:NULL;
        const uint8_t*lower=from_bottom<size?shadow_bottom_left+(size_t)(size-1-from_bottom)*(size_t)size:NULL;
        /* On a low card both corners reach this row, so they fade into each other. */
        unsigned int weight=y<=top?0u:(y>=bottom?256u:(unsigned int)((y-top)*256/(bottom-top)));
        if(!upper&&!lower){upper=lower=shadow_left;columns=SHADOW_MARGIN;}
        else if(!upper)upper=lower;
        else if(!lower)lower=upper;
        if(y<top)edge=from_top<size?shadow_top[from_top]:0;
        else if(y>=bottom)edge=from_bottom<size?shadow_bottom[size-1-from_bottom]:0;
        /* Beside a card the shadow ends at its edge, and around a corner at the end of the curve. */
        int visible=columns,curve=(int)ceilf(radius);
        if(y>=top&&y<bottom){int reach=y>=top+curve&&y<bottom-curve?SHADOW_MARGIN:SHADOW_MARGIN+curve+1;if(reach<visible)visible=reach;}
        for(int n=0;n<visible;n++){
            unsigned int value=((upper[n]*(256u-weight)+lower[n]*weight)>>8)*strength>>8;
            if(!value)continue;
            int l=x0+n,r=x1-1-n;
            if(l<middle_x&&l>=b.x0&&l<b.x1){unsigned int shown=canvas_shadow_outside(l,y,left,top,right,bottom,radius,value);if(shown)row[l]=canvas_blend(row[l],color,shown);}
            if(r>=middle_x&&r>=b.x0&&r<b.x1){unsigned int shown=canvas_shadow_outside(r,y,left,top,right,bottom,radius,value);if(shown)row[r]=canvas_blend(row[r],color,shown);}
        }
        if(edge>0){
            unsigned int value=(unsigned int)edge*strength>>8;int from=x0+size,to=x1-size;
            if(from<b.x0)from=b.x0;
            if(to>b.x1)to=b.x1;
            if(value&&to>from)canvas_span(row,from,to,color,value);
        }
    }
}
/* A picture at its own size with rounded corners: the small phones in the theme list. */
static void canvas_picture(uint32_t*out,Bounds b,int x0,int y0,const uint8_t*source,int width,int height,float radius){
    for(int y=0;y<height;y++){
        int py=y0+y;if(py<b.y0||py>=b.y1)continue;
        float dy=(float)y+.5f<radius?radius-(float)y-.5f:((float)(height-y)-.5f<radius?radius-(float)(height-y)+.5f:0.f);
        for(int x=0;x<width;x++){
            int px=x0+x;if(px<b.x0||px>=b.x1)continue;
            float dx=(float)x+.5f<radius?radius-(float)x-.5f:((float)(width-x)-.5f<radius?radius-(float)(width-x)+.5f:0.f);
            float keep=dx>0&&dy>0?radius-sqrtf(dx*dx+dy*dy)+.5f:1.f;if(keep<=0)continue;if(keep>1)keep=1;
            const uint8_t*s=source+((size_t)y*(size_t)width+(size_t)x)*3;uint32_t*d=out+(size_t)py*NW+px;uint32_t old=*d;
            uint32_t red=(uint32_t)((float)s[0]*keep+(float)((old>>16)&255)*(1-keep)),green=(uint32_t)((float)s[1]*keep+(float)((old>>8)&255)*(1-keep)),blue=(uint32_t)((float)s[2]*keep+(float)(old&255)*(1-keep));
            *d=red<<16|green<<8|blue;
        }
    }
}
/* The 8x8 ordered dither of the backdrop and the frosted copy, one table for both (2026-11-14 review). */
static const unsigned char canvas_bayer[8][8]={{0,32,8,40,2,34,10,42},{48,16,56,24,50,18,58,26},{12,44,4,36,14,46,6,38},{60,28,52,20,62,30,54,22},
    {3,35,11,43,1,33,9,41},{51,19,59,27,49,17,57,25},{15,47,7,39,13,45,5,37},{63,31,55,23,61,29,53,21}};
/* Stretch a small backdrop to the panel. Ordered dither keeps the soft gradient from banding.
 * Panel rows between two source rows share them, so those are mixed across once for all of them. */
static void canvas_backdrop(uint32_t*out,const uint8_t*source,int width,int height){
    static float top[3][NW],bottom[3][NW],noise[8][NW];static bool noise_ready;
    /* Worked out once, not at every call (2026-11-14 review). */
    if(!noise_ready){for(int y=0;y<8;y++)for(int x=0;x<NW;x++)noise[y][x]=(float)canvas_bayer[y][x&7]/64.f;noise_ready=true;}
    int mixed=-1;
    for(int y=0;y<NH;y++){
        float fy=((float)y+.5f)*(float)height/(float)NH-.5f;fy=fy<0?0:fy;int y0=(int)fy,y1=y0+1<height?y0+1:y0;float ty=fy-(float)y0,keep=1-ty;
        if(y0!=mixed){
            mixed=y0;
            for(int x=0;x<NW;x++){
                float fx=((float)x+.5f)*(float)width/(float)NW-.5f;fx=fx<0?0:fx;int x0=(int)fx,x1=x0+1<width?x0+1:x0;float tx=fx-(float)x0;
                for(int c=0;c<3;c++){
                    top[c][x]=(float)source[(y0*width+x0)*3+c]*(1-tx)+(float)source[(y0*width+x1)*3+c]*tx;
                    bottom[c][x]=(float)source[(y1*width+x0)*3+c]*(1-tx)+(float)source[(y1*width+x1)*3+c]*tx;
                }
            }
        }
        const float*dither=noise[y&7];uint32_t*row=out+(size_t)y*NW;
        for(int x=0;x<NW;x++){
            int red=(int)(top[0][x]*keep+bottom[0][x]*ty+dither[x]),green=(int)(top[1][x]*keep+bottom[1][x]*ty+dither[x]),blue=(int)(top[2][x]*keep+bottom[2][x]*ty+dither[x]);
            row[x]=(uint32_t)(red>255?255:red)<<16|(uint32_t)(green>255?255:green)<<8|(uint32_t)(blue>255?255:blue);
        }
    }
}
/* One run of two frames mixed: canvas_blend in a form the compiler turns into vector code. */
static inline void canvas_mix_run(uint32_t*restrict out,const uint32_t*restrict from,const uint32_t*restrict to,int count,unsigned int alpha){
    const uint32_t over=alpha+(alpha>>7),rest=256u-over;
    for(int x=0;x<count;x++){
        uint32_t a=from[x],b=to[x];
        out[x]=((((b&0xff00ffu)*over+(a&0xff00ffu)*rest)>>8)&0xff00ffu)|((((b&0x00ff00u)*over+(a&0x00ff00u)*rest)>>8)&0x00ff00u);
    }
}
#define BLUR_STEP 8
#define BLUR_W (NW/BLUR_STEP)
#define BLUR_H (NH/BLUR_STEP)
#define BLUR_RADIUS 6
/* One soft pass over the small picture: a running sum along each line, forwards and back. */
static void canvas_blur_line(uint16_t*line,int count,int stride){
    static uint16_t copy[BLUR_H>BLUR_W?BLUR_H:BLUR_W];
    for(int n=0;n<count;n++)copy[n]=line[n*stride];
    uint32_t sum=0;
    for(int n=-BLUR_RADIUS;n<=BLUR_RADIUS;n++)sum+=copy[n<0?0:(n>=count?count-1:n)];
    for(int n=0;n<count;n++){
        line[n*stride]=(uint16_t)(sum/(2*BLUR_RADIUS+1));
        int leaving=n-BLUR_RADIUS,entering=n+BLUR_RADIUS+1;
        sum+=copy[entering>=count?count-1:entering];sum-=copy[leaving<0?0:leaving];
    }
}
/* A frosted copy of a frame, made once and shown as a still picture: reduced eight times,
 * softened twice each way, and stretched back with dither so the gradients do not band. */
static void canvas_blur(uint32_t*restrict out,const uint32_t*restrict source){
    static uint16_t plane[3][BLUR_H][BLUR_W];
    static uint32_t upper[3][NW],lower[3][NW],noise[8][NW];
    static int first[NW],second[NW],weight[NW];
    for(int y=0;y<BLUR_H;y++)for(int x=0;x<BLUR_W;x++){
        uint32_t red=0,green=0,blue=0;
        for(int dy=0;dy<BLUR_STEP;dy+=2){
            const uint32_t*row=source+(size_t)(y*BLUR_STEP+dy)*NW+(size_t)x*BLUR_STEP;
            for(int dx=0;dx<BLUR_STEP;dx+=2){uint32_t p=row[dx];red+=(p>>16)&255u;green+=(p>>8)&255u;blue+=p&255u;}
        }
        plane[0][y][x]=(uint16_t)red;plane[1][y][x]=(uint16_t)green;plane[2][y][x]=(uint16_t)blue;
    }
    for(int c=0;c<3;c++)for(int pass=0;pass<2;pass++){
        for(int y=0;y<BLUR_H;y++)canvas_blur_line(plane[c][y],BLUR_W,1);
        for(int x=0;x<BLUR_W;x++)canvas_blur_line(&plane[c][0][x],BLUR_H,BLUR_W);
    }
    for(int x=0;x<NW;x++){
        int position=2*x+1-BLUR_STEP,cell=position<0?-1:position/(2*BLUR_STEP);
        weight[x]=position-cell*2*BLUR_STEP;first[x]=cell<0?0:cell;second[x]=cell+1<BLUR_W?cell+1:BLUR_W-1;
        for(int y=0;y<8;y++)noise[y][x]=(uint32_t)canvas_bayer[y][x&7]*64u;
    }
    int mixed=-2;
    for(int y=0;y<NH;y++){
        int position=2*y+1-BLUR_STEP,cell=position<0?-1:position/(2*BLUR_STEP);uint32_t fall=(uint32_t)(position-cell*2*BLUR_STEP),rise=16u-fall;
        if(cell!=mixed){
            mixed=cell;int top=cell<0?0:cell,bottom=cell+1<BLUR_H?cell+1:BLUR_H-1;
            for(int c=0;c<3;c++)for(int x=0;x<NW;x++){
                uint32_t right=(uint32_t)weight[x],left=16u-right;
                upper[c][x]=plane[c][top][first[x]]*left+plane[c][top][second[x]]*right;
                lower[c][x]=plane[c][bottom][first[x]]*left+plane[c][bottom][second[x]]*right;
            }
        }
        const uint32_t*dither=noise[y&7];uint32_t*row=out+(size_t)y*NW;
        /* Sixteen samples and weights that sum to sixteen leave 256 parts to a level. */
        for(int x=0;x<NW;x++){
            uint32_t red=(upper[0][x]*rise+lower[0][x]*fall)/16u,green=(upper[1][x]*rise+lower[1][x]*fall)/16u,blue=(upper[2][x]*rise+lower[2][x]*fall)/16u;
            red=(red+dither[x]/16u)>>8;green=(green+dither[x]/16u)>>8;blue=(blue+dither[x]/16u)>>8;
            row[x]=(red>255u?255u:red)<<16|(green>255u?255u:green)<<8|(blue>255u?255u:blue);
        }
    }
}
static void canvas_mask(uint32_t*pixels,Bounds b,int x,int y,int width,int height,const uint8_t*alpha,uint32_t color){
    int left=x<b.x0?b.x0-x:0,top=y<b.y0?b.y0-y:0,right=x+width>b.x1?b.x1-x:width,bottom=y+height>b.y1?b.y1-y:height;
    for(int row=top;row<bottom;row++){
        uint32_t*out=pixels+(size_t)(y+row)*NW+x;const uint8_t*in=alpha+(size_t)row*(size_t)width;
        for(int column=left;column<right;column++){unsigned int value=in[column];if(value)out[column]=canvas_blend(out[column],color,value);}
    }
}
#endif
