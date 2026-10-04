/* Private bounded App numeric JSON grammar observation. Finite decimal literals
 * remain decimal nodes; integer clocks/counts retain their exact int64 type.
 * Parsing observes bytes only. It never establishes an App/Attempt hold,
 * operator permission, PM quiescence or a server lease. No device operations.
 */
#ifndef HG_APP_NUMERIC_JSON_H
#define HG_APP_NUMERIC_JSON_H
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <limits.h>
#include <stdlib.h>
#include <math.h>
#include <locale.h>
#define AJ_BYTES 65536
#define AJ_NODES 8192
#define AJ_DEPTH 8
enum aj_kind { AJ_OBJECT=1,AJ_ARRAY,AJ_STRING,AJ_INTEGER,AJ_TRUE,AJ_FALSE,AJ_NULL,AJ_DECIMAL };
struct aj_node {enum aj_kind kind;size_t at,len;unsigned next,count;int64_t integer;};
struct aj_doc {const unsigned char *raw;size_t bytes,pos;unsigned used;struct aj_node node[AJ_NODES];};
static void aj_space(struct aj_doc *d) {
    while(d->pos<d->bytes&&(d->raw[d->pos]==' '||d->raw[d->pos]=='\n'||d->raw[d->pos]=='\r'||d->raw[d->pos]=='\t')) ++d->pos;
}
static int aj_value(struct aj_doc *d,unsigned depth,unsigned *index) {
    aj_space(d);if(depth>AJ_DEPTH||d->pos>=d->bytes||d->used==AJ_NODES)return 0;
    unsigned i=d->used++;*index=i;struct aj_node *n=&d->node[i];n->at=d->pos;
    unsigned char c=d->raw[d->pos++];
    if(c=='{'||c=='['){
        n->kind=c=='{'?AJ_OBJECT:AJ_ARRAY;unsigned char close=c=='{'?'}':']';
        aj_space(d);if(d->pos<d->bytes&&d->raw[d->pos]==close){++d->pos;n->next=d->used;return 1;}
        for(;;){unsigned child;
            if(!aj_value(d,depth+1,&child))return 0;
            if(n->kind==AJ_OBJECT){
                if(d->node[child].kind!=AJ_STRING)return 0;
                /* Keys are literal producer ASCII; escapes are rejected, so
                 * duplicate aliases cannot hide behind unicode/escape forms. */
                for(unsigned old=i+1;old<child;old=d->node[d->node[old].next].next)
                    if(d->node[old].len==d->node[child].len&&!memcmp(d->raw+d->node[old].at,d->raw+d->node[child].at,d->node[old].len))return 0;
                aj_space(d);if(d->pos>=d->bytes||d->raw[d->pos++]!=':')return 0;
                unsigned value;if(!aj_value(d,depth+1,&value)||d->node[value].kind==AJ_STRING
                        ||d->node[value].kind==AJ_TRUE||d->node[value].kind==AJ_FALSE||d->node[value].kind==AJ_NULL)return 0;
            }
            if(n->kind==AJ_ARRAY&&(d->node[child].kind==AJ_STRING||d->node[child].kind==AJ_TRUE
                    ||d->node[child].kind==AJ_FALSE||d->node[child].kind==AJ_NULL))return 0;
            ++n->count;aj_space(d);if(d->pos>=d->bytes)return 0;
            c=d->raw[d->pos++];if(c==close)break;if(c!=',')return 0;
        }
    }else if(c=='"'){
        n->kind=AJ_STRING;n->at=d->pos;
        while(d->pos<d->bytes&&d->raw[d->pos]!='"'){
            c=d->raw[d->pos++];if(c<32||c>126||c=='\\')return 0;
        }
        if(d->pos>=d->bytes)return 0;n->len=d->pos-n->at;++d->pos;
    }else if(c=='t'||c=='f'||c=='n'){
        const char *s=c=='t'?"true":c=='f'?"false":"null";size_t len=strlen(s);
        if(d->bytes-n->at<len||memcmp(d->raw+n->at,s,len))return 0;
        d->pos=n->at+len;n->kind=c=='t'?AJ_TRUE:c=='f'?AJ_FALSE:AJ_NULL;
    }else{
        d->pos=n->at;int negative=0;if(d->raw[d->pos]=='-'){negative=1;++d->pos;}
        if(d->pos>=d->bytes||d->raw[d->pos]<'0'||d->raw[d->pos]>'9')return 0;
        size_t start=d->pos;
        while(d->pos<d->bytes&&d->raw[d->pos]>='0'&&d->raw[d->pos]<='9')++d->pos;
        if(d->pos-start>1&&d->raw[start]=='0')return 0;
        int decimal=0;
        if(d->pos<d->bytes&&d->raw[d->pos]=='.'){
            decimal=1;++d->pos;size_t fraction=d->pos;
            while(d->pos<d->bytes&&d->raw[d->pos]>='0'&&d->raw[d->pos]<='9')++d->pos;
            if(fraction==d->pos)return 0;
        }
        if(d->pos<d->bytes&&(d->raw[d->pos]=='e'||d->raw[d->pos]=='E')){
            decimal=1;++d->pos;
            if(d->pos<d->bytes&&(d->raw[d->pos]=='+'||d->raw[d->pos]=='-'))++d->pos;
            size_t exponent=d->pos;
            while(d->pos<d->bytes&&d->raw[d->pos]>='0'&&d->raw[d->pos]<='9')++d->pos;
            if(exponent==d->pos)return 0;
        }
        n->len=d->pos-n->at;if(n->len>128)return 0;
        if(decimal){
            if(strcmp(localeconv()->decimal_point,"."))return 0;
            char literal[129],*finish;memcpy(literal,d->raw+n->at,n->len);literal[n->len]=0;
            double observed=strtod(literal,&finish);
            if(finish!=literal+n->len||!isfinite(observed))return 0;
            n->kind=AJ_DECIMAL; /* Never converted into an integral clock/count. */
        }else{
            uint64_t value=0,limit=(uint64_t)INT64_MAX+(unsigned)negative;
            for(size_t j=start;j<d->pos;++j){unsigned digit=d->raw[j]-'0';
                if(value>(limit-digit)/10)return 0;value=value*10+digit;}
            if(negative&&!value)return 0;
            n->kind=AJ_INTEGER;n->integer=negative?(value==(uint64_t)INT64_MAX+1?INT64_MIN:-(int64_t)value):(int64_t)value;
        }
    }
    n->next=d->used;return 1;
}
static int aj_parse(struct aj_doc *d,const unsigned char *raw,size_t bytes) {
    if(!d||!raw||!bytes||bytes>AJ_BYTES)return 0;memset(d,0,sizeof(*d));d->raw=raw;d->bytes=bytes;
    unsigned i;if(!aj_value(d,0,&i))return 0;aj_space(d);return d->pos==bytes&&d->node[0].kind==AJ_OBJECT;
}
static int aj_text(const struct aj_doc *d,unsigned i,const char *text) {
    return i<d->used&&d->node[i].kind==AJ_STRING&&d->node[i].len==strlen(text)&&!memcmp(d->raw+d->node[i].at,text,strlen(text));
}
static unsigned aj_get(const struct aj_doc *d,unsigned object,const char *key) {
    if(object>=d->used||d->node[object].kind!=AJ_OBJECT)return AJ_NODES;
    for(unsigned i=object+1;i<d->node[object].next;i=d->node[d->node[i].next].next)
        if(aj_text(d,i,key))return d->node[i].next;
    return AJ_NODES;
}
static int aj_num(const struct aj_doc *d,unsigned object,const char *key,int64_t low,int64_t high,int64_t *out) {
    unsigned i=aj_get(d,object,key);if(i>=d->used||d->node[i].kind!=AJ_INTEGER||d->node[i].integer<low||d->node[i].integer>high)return 0;
    if(out)*out=d->node[i].integer;return 1;
}
static int aj_flag(const struct aj_doc *d,const char *key,enum aj_kind kind) {
    unsigned i=aj_get(d,0,key);return i<d->used&&d->node[i].kind==kind;
}
#endif
