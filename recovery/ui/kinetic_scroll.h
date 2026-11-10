/* SPDX-License-Identifier: MIT */
#ifndef A22X_KINETIC_SCROLL_H
#define A22X_KINETIC_SCROLL_H
#include <stdbool.h>
#include <stdint.h>

#define SCROLL_SAMPLES 12
#define SCROLL_MIN_SPEED 180.0
#define SCROLL_MAX_SPEED 3500.0
#define SCROLL_DECELERATION 4200.0
typedef struct {uint64_t time;double position;} ScrollSample;
typedef struct {ScrollSample samples[SCROLL_SAMPLES];unsigned int count;} ScrollSamples;
typedef struct {bool active;double position,velocity;uint64_t last_time,started_at;} KineticScroll;

static double scroll_abs(double value){return value<0?-value:value;}
static double scroll_clamp(double value,double maximum){return value<0?0:(value>maximum?maximum:value);}
static void scroll_sample(ScrollSamples*s,uint64_t now,double position){
    if(s->count&&now<s->samples[s->count-1].time)s->count=0;
    if(s->count&&now==s->samples[s->count-1].time){s->samples[s->count-1].position=position;return;}
    if(s->count==SCROLL_SAMPLES){for(unsigned int n=1;n<s->count;n++)s->samples[n-1]=s->samples[n];s->count--;}
    s->samples[s->count++]=(ScrollSample){now,position};
}
static double scroll_release_velocity(const ScrollSamples*s,uint64_t now){
    if(s->count<2)return 0;
    const ScrollSample*last=&s->samples[s->count-1];
    if(now<last->time||now-last->time>100)return 0;
    unsigned int first=0;while(first+1<s->count&&now-s->samples[first].time>100)first++;
    uint64_t span=last->time-s->samples[first].time;if(span<12)return 0;
    double velocity=(last->position-s->samples[first].position)*1000.0/(double)span;
    if(velocity>SCROLL_MAX_SPEED)velocity=SCROLL_MAX_SPEED;
    if(velocity<-SCROLL_MAX_SPEED)velocity=-SCROLL_MAX_SPEED;
    return scroll_abs(velocity)>=SCROLL_MIN_SPEED?velocity:0;
}
static bool kinetic_begin(KineticScroll*k,double position,double velocity,double maximum,uint64_t now){
    if(velocity>SCROLL_MAX_SPEED)velocity=SCROLL_MAX_SPEED;
    if(velocity<-SCROLL_MAX_SPEED)velocity=-SCROLL_MAX_SPEED;
    *k=(KineticScroll){.position=scroll_clamp(position,maximum),.velocity=velocity,.last_time=now,.started_at=now};
    if(scroll_abs(velocity)<SCROLL_MIN_SPEED||maximum<=0||(k->position<=0&&velocity<0)||(k->position>=maximum&&velocity>0))return false;
    k->active=true;return true;
}
static bool kinetic_advance(KineticScroll*k,uint64_t now,double maximum){
    if(!k->active||now<=k->last_time)return false;
    double before=k->position,seconds=(double)(now-k->last_time)/1000.0;
    double speed=scroll_abs(k->velocity),sign=k->velocity<0?-1.0:1.0;
    double moving=seconds<speed/SCROLL_DECELERATION?seconds:speed/SCROLL_DECELERATION;
    k->position+=k->velocity*moving-sign*SCROLL_DECELERATION*moving*moving*.5;
    speed-=SCROLL_DECELERATION*seconds;if(speed<0)speed=0;
    k->velocity=sign*speed;k->last_time=now;
    if(k->position<=0){k->position=0;k->velocity=0;}
    if(k->position>=maximum){k->position=maximum;k->velocity=0;}
    k->active=k->velocity!=0;
    return before!=k->position;
}
#endif
