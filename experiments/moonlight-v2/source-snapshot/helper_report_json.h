/* Bounded reader for the pinned single-window helper's ASCII numeric JSON.
 * Parsing observes bytes only. It never establishes an App/Attempt hold,
 * operator permission, PM quiescence or a server lease. No device operations.
 */
#ifndef HG_HELPER_REPORT_JSON_H
#define HG_HELPER_REPORT_JSON_H
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <limits.h>
#define HJ_BYTES 65536
#define HJ_NODES 512
#define HJ_DEPTH 8
enum hj_kind { HJ_OBJECT=1,HJ_ARRAY,HJ_STRING,HJ_INTEGER,HJ_TRUE,HJ_FALSE,HJ_NULL };
struct hj_node {enum hj_kind kind;size_t at,len;unsigned next,count;int64_t integer;};
struct hj_doc {const unsigned char *raw;size_t bytes,pos;unsigned used;struct hj_node node[HJ_NODES];};
static void hj_space(struct hj_doc *d) {
    while(d->pos<d->bytes&&(d->raw[d->pos]==' '||d->raw[d->pos]=='\n'||d->raw[d->pos]=='\r'||d->raw[d->pos]=='\t')) ++d->pos;
}
static int hj_value(struct hj_doc *d,unsigned depth,unsigned *index) {
    hj_space(d);if(depth>HJ_DEPTH||d->pos>=d->bytes||d->used==HJ_NODES)return 0;
    unsigned i=d->used++;*index=i;struct hj_node *n=&d->node[i];n->at=d->pos;
    unsigned char c=d->raw[d->pos++];
    if(c=='{'||c=='['){
        n->kind=c=='{'?HJ_OBJECT:HJ_ARRAY;unsigned char close=c=='{'?'}':']';
        hj_space(d);if(d->pos<d->bytes&&d->raw[d->pos]==close){++d->pos;n->next=d->used;return 1;}
        for(;;){unsigned child;
            if(!hj_value(d,depth+1,&child))return 0;
            if(n->kind==HJ_OBJECT){
                if(d->node[child].kind!=HJ_STRING)return 0;
                /* Keys are literal producer ASCII; escapes are rejected, so
                 * duplicate aliases cannot hide behind unicode/escape forms. */
                for(unsigned old=i+1;old<child;old=d->node[d->node[old].next].next)
                    if(d->node[old].len==d->node[child].len&&!memcmp(d->raw+d->node[old].at,d->raw+d->node[child].at,d->node[old].len))return 0;
                hj_space(d);if(d->pos>=d->bytes||d->raw[d->pos++]!=':')return 0;
                unsigned value;if(!hj_value(d,depth+1,&value))return 0;
            }
            ++n->count;hj_space(d);if(d->pos>=d->bytes)return 0;
            c=d->raw[d->pos++];if(c==close)break;if(c!=',')return 0;
        }
    }else if(c=='"'){
        n->kind=HJ_STRING;n->at=d->pos;
        while(d->pos<d->bytes&&d->raw[d->pos]!='"'){
            c=d->raw[d->pos++];if(c<32||c>126||c=='\\')return 0;
        }
        if(d->pos>=d->bytes)return 0;n->len=d->pos-n->at;++d->pos;
    }else if(c=='t'||c=='f'||c=='n'){
        const char *s=c=='t'?"true":c=='f'?"false":"null";size_t len=strlen(s);
        if(d->bytes-n->at<len||memcmp(d->raw+n->at,s,len))return 0;
        d->pos=n->at+len;n->kind=c=='t'?HJ_TRUE:c=='f'?HJ_FALSE:HJ_NULL;
    }else{
        d->pos=n->at;int negative=0;if(d->raw[d->pos]=='-'){negative=1;++d->pos;}
        if(d->pos>=d->bytes||d->raw[d->pos]<'0'||d->raw[d->pos]>'9')return 0;
        size_t start=d->pos;uint64_t value=0,limit=(uint64_t)INT64_MAX+(unsigned)negative;
        while(d->pos<d->bytes&&d->raw[d->pos]>='0'&&d->raw[d->pos]<='9'){
            unsigned digit=d->raw[d->pos++]-'0';if(value>(limit-digit)/10)return 0;value=value*10+digit;
        }
        if((d->pos-start>1&&d->raw[start]=='0')||(negative&&!value))return 0;
        n->kind=HJ_INTEGER;n->integer=negative?(value==(uint64_t)INT64_MAX+1?INT64_MIN:-(int64_t)value):(int64_t)value;
    }
    n->next=d->used;return 1;
}
static int hj_parse(struct hj_doc *d,const unsigned char *raw,size_t bytes) {
    if(!d||!raw||!bytes||bytes>HJ_BYTES)return 0;memset(d,0,sizeof(*d));d->raw=raw;d->bytes=bytes;
    unsigned i;if(!hj_value(d,0,&i))return 0;hj_space(d);return d->pos==bytes&&d->node[0].kind==HJ_OBJECT;
}
static int hj_text(const struct hj_doc *d,unsigned i,const char *text) {
    return i<d->used&&d->node[i].kind==HJ_STRING&&d->node[i].len==strlen(text)&&!memcmp(d->raw+d->node[i].at,text,strlen(text));
}
static unsigned hj_get(const struct hj_doc *d,unsigned object,const char *key) {
    if(object>=d->used||d->node[object].kind!=HJ_OBJECT)return HJ_NODES;
    for(unsigned i=object+1;i<d->node[object].next;i=d->node[d->node[i].next].next)
        if(hj_text(d,i,key))return d->node[i].next;
    return HJ_NODES;
}
static int hj_num(const struct hj_doc *d,unsigned object,const char *key,int64_t low,int64_t high,int64_t *out) {
    unsigned i=hj_get(d,object,key);if(i>=d->used||d->node[i].kind!=HJ_INTEGER||d->node[i].integer<low||d->node[i].integer>high)return 0;
    if(out)*out=d->node[i].integer;return 1;
}
static int hj_flag(const struct hj_doc *d,const char *key,enum hj_kind kind) {
    unsigned i=hj_get(d,0,key);return i<d->used&&d->node[i].kind==kind;
}
#endif
