'use client';
import {useEffect,useRef,useState} from 'react';
import {get,post,getErrorMessage} from '@/lib/http';
export type JobMode = 'individual'|'compare'|'statistics';
export interface Criterion {field:string;operator:string;value:string|number}
export interface BatchAnalysis {
  population:number;exact:boolean;criteria:Criterion[];computed_at:string;basis:string;
  matched_count:number;not_matched_count:number;unknown_count:number;coverage_incomplete:boolean;
  matched_ids:number[];not_matched_ids:number[];unknown_ids:number[];
  rows:{person_id:number;profile_version:number;name?:string;status:string;source_partial:boolean;
    fields:Record<string,{value:string|number|string[]|null;field:string;profile_version:number;snapshot_at?:string}>}[];
}

export interface JobAnswer {
  person_id:number; status:string; answer?:string; error?:string; profile_version:number; question_id?:number;
  source_coverage?:string;
  citations:{citation_id:number;filename?:string;file_id:number;page_no:number|null;snippet:string}[];
}
export interface QuestionJob {
  job_id:number; status:string; question:string; requested:number; processed:number; answered:number;
  items:JobAnswer[]; error?:string; follow_up_to?:number|null;
  mode?:JobMode;criteria?:Criterion[];analysis?:BatchAnalysis|null;
}
export function useQuestionJob(personIds:number[],mode:JobMode='individual') {
  const key=personIds.join(',');
  const [job,setJob]=useState<QuestionJob|null>(null);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const sequence=useRef(0);
  const timer=useRef<ReturnType<typeof setTimeout>|null>(null);
  const request=useRef<{fingerprint:string;id:string}|null>(null);
  const stop=()=>{sequence.current++;if(timer.current)clearTimeout(timer.current);timer.current=null;};
  const accept=(value:QuestionJob,seq:number)=>{
    if(seq!==sequence.current)return;
    setJob(value);setError('');
    const active=['pending','running'].includes(value.status);
    setBusy(active);
    if(!active)request.current=null;
    if(value.status==='failed')setError(value.error||'问答任务未完成');
    if(active)timer.current=setTimeout(()=>void poll(value.job_id,seq),1500);
  };
  const poll=async(id:number,seq:number)=>{
    try {const value=await get<QuestionJob>(`/talents/question-jobs/${id}`);accept(value,seq);}
    catch(e){if(seq===sequence.current){setBusy(false);setError(getErrorMessage(e));
      if([401,403,409].includes((e as {code?:number}).code||0))setJob(null);}}
  };
  useEffect(()=>{
    stop();const seq=sequence.current;setJob(null);setError('');setBusy(false);request.current=null;
    if(key)void (async()=>{
      try {const query=key.split(',').map(id=>`person_ids=${encodeURIComponent(id)}`).join('&');
        const value=await get<QuestionJob|null>(`/talents/question-jobs?${query}${mode==='individual'?'':`&mode=${mode}`}`);
        if(value)accept(value,seq);
      }catch(e){if(seq===sequence.current)setError(getErrorMessage(e));}
    })();
    return stop;
    // The ordered person set defines this view; callbacks use the sequence guard.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[key,mode]);
  const submit=async(payload:{question:string;version?:number;follow_up_to?:number;criteria?:Criterion[]})=>{
    stop();const seq=sequence.current;setBusy(true);setError('');setJob(null);
    const fingerprint=JSON.stringify([key,mode,payload]);
    if(request.current?.fingerprint!==fingerprint)request.current={fingerprint,id:crypto.randomUUID()};
    try {const value=await post<QuestionJob>('/talents/question-jobs',{
      person_ids:personIds,...payload,...(mode==='individual'?{}:{mode}),idempotency_key:request.current.id});accept(value,seq);}
    catch(e){if(seq===sequence.current){setBusy(false);setError(getErrorMessage(e));}}
  };
  const cancel=async()=>{
    if(!job)return;
    stop();const seq=sequence.current;setBusy(true);
    try {const value=await post<{status:string}>(`/talents/question-jobs/${job.job_id}/cancel`);
      if(seq===sequence.current){
        if(value.status==='completed'){await poll(job.job_id,seq);return;}
        setJob({...job,status:value.status});request.current=null;setBusy(false);setError('');}}
    catch(e){if(seq===sequence.current){setBusy(false);setError(getErrorMessage(e));}}
  };
  const resume=()=>{if(job){stop();setBusy(true);void poll(job.job_id,sequence.current);}};
  const clear=()=>{stop();request.current=null;setJob(null);setError('');setBusy(false);};
  return {job,busy,error,submit,cancel,resume,clear};
}
