#include "../../experiments/moonlight-v2/source-snapshot/helper_App_fields_json.h"
#include <stdio.h>
int main(void){unsigned char raw[65537];size_t n=fread(raw,1,sizeof(raw),stdin);struct aj_doc *d=calloc(1,sizeof(*d));struct af_claims out;
 int good=d&&af_core_json(d,raw,n,&out);printf("{\"observed\":%s,\"native_available\":%d,\"native_enabled\":%d,\"unqualified_optional_objects\":%u,\"permission\":false,\"release\":false}\n",good?"true":"false",good?out.native_available:-1,good?out.native_enabled:-1,good?out.unqualified_optional_objects:0);free(d);return good?0:2;}
