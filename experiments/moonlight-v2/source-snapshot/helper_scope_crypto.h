/* SHA/closed numeric helpers copied from exact ab1c6f2e retirement source.
 * A NEW owner artifact depends on these bytes; the old standalone stays intact.
 */
#ifndef HG_HELPER_SCOPE_CRYPTO_H
#define HG_HELPER_SCOPE_CRYPTO_H
static const uint32_t k[64] = {
  0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
  0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
  0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
  0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
  0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
  0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
  0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
  0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2};
struct sha { uint32_t h[8]; unsigned char block[64]; size_t used; uint64_t bytes; };
static uint32_t rotr(uint32_t x, unsigned n) { return (x >> n) | (x << (32 - n)); }
static void block(struct sha *s, const unsigned char *p) {
    uint32_t w[64];
    for (int i=0;i<16;++i) w[i]=(uint32_t)p[i*4]<<24|(uint32_t)p[i*4+1]<<16|(uint32_t)p[i*4+2]<<8|p[i*4+3];
    for (int i=16;i<64;++i) {
        uint32_t a=w[i-15],b=w[i-2];
        w[i]=w[i-16]+(rotr(a,7)^rotr(a,18)^(a>>3))+w[i-7]+(rotr(b,17)^rotr(b,19)^(b>>10));
    }
    uint32_t a=s->h[0],b=s->h[1],c=s->h[2],d=s->h[3],e=s->h[4],f=s->h[5],g=s->h[6],h=s->h[7];
    for (int i=0;i<64;++i) {
        uint32_t t=h+(rotr(e,6)^rotr(e,11)^rotr(e,25))+((e&f)^((~e)&g))+k[i]+w[i];
        uint32_t u=(rotr(a,2)^rotr(a,13)^rotr(a,22))+((a&b)^(a&c)^(b&c));
        h=g;g=f;f=e;e=d+t;d=c;c=b;b=a;a=t+u;
    }
    s->h[0]+=a;s->h[1]+=b;s->h[2]+=c;s->h[3]+=d;s->h[4]+=e;s->h[5]+=f;s->h[6]+=g;s->h[7]+=h;
}
static void update(struct sha *s,const unsigned char *p,size_t n) {
    s->bytes+=n;
    while (n) {
        size_t take=64-s->used;if (take>n) take=n;
        memcpy(s->block+s->used,p,take);s->used+=take;p+=take;n-=take;
        if (s->used==64) {block(s,s->block);s->used=0;}
    }
}
static void digest(struct sha *s,char out[65]) {
    uint64_t bits=s->bytes*8;unsigned char pad[128]={0x80};
    size_t n=s->used<56?56-s->used:120-s->used;
    for (int i=0;i<8;++i) pad[n+i]=(unsigned char)(bits>>(56-i*8));
    update(s,pad,n+8);
    for (int i=0;i<8;++i) snprintf(out+i*8,9,"%08" PRIx32,s->h[i]);
}
#ifdef HG_HELPER_OWNER_FIXTURE
static int number(const char *p,uint64_t *v) {
    if (!p || !*p || strlen(p)>19) return 0;
    for (const char *q=p;*q;++q) if (*q<'0'||*q>'9') return 0;
    char *end;errno=0;unsigned long long x=strtoull(p,&end,10);
    if (errno||*end||x>INT64_MAX) return 0;
    *v=x;return 1;
}
#endif
static int hex(const char *p,size_t n) {
    if (!p || strlen(p)!=n) return 0;
    for (size_t i=0;i<n;++i) if (!((p[i]>='0'&&p[i]<='9')||(p[i]>='a'&&p[i]<='f'))) return 0;
    return 1;
}
static uint64_t now_ms(void) {
    struct timespec t;if (clock_gettime(CLOCK_MONOTONIC,&t)) return 0;
    return (uint64_t)t.tv_sec*1000+(uint64_t)t.tv_nsec/1000000;
}
static int within(uint64_t end) { uint64_t n=now_ms();return n&&n<end; }

#endif
