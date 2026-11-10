#ifndef A22X_JSON_MIN_H
#define A22X_JSON_MIN_H
#include <stddef.h>
#include <stdint.h>
enum { J_OBJECT=1, J_ARRAY, J_STRING, J_PRIMITIVE };
typedef struct { int type,start,end,parent; } JsonToken;
typedef struct { const char *text; JsonToken *tokens; int count,capacity; } Json;
int json_parse(Json *j,const char *text,JsonToken *tokens,int capacity);
int json_get(const Json *j,int obj,const char *key);
int json_next(const Json *j,int token);
int json_string(const Json *j,int token,char *out,size_t size);
int json_bool(const Json *j,int token,int fallback);
uint64_t json_uint(const Json *j,int token,uint64_t fallback);
#endif
