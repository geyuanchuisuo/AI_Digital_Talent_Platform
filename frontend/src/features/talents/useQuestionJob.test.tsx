import {act,renderHook,waitFor} from '@testing-library/react';
import {beforeEach,describe,expect,it,vi} from 'vitest';
const {get,post}=vi.hoisted(()=>({get:vi.fn(),post:vi.fn()}));
vi.mock('@/lib/http',()=>({get,post,getErrorMessage:()=> '连接失败'}));
import {useQuestionJob} from './useQuestionJob';
const pending={job_id:7,status:'running',question:'技能？',items:[],requested:1,processed:0,answered:0};
beforeEach(()=>{get.mockReset();post.mockReset();get.mockResolvedValue(null);});

describe('可恢复问答任务',()=>{
  it('切换模式隔离恢复请求，并将条件变化计入幂等键',async()=>{
    post.mockRejectedValue(new Error('offline'));
    const {result,rerender}=renderHook(({mode})=>useQuestionJob([12,13],mode),{initialProps:{mode:'statistics' as 'statistics'|'compare'}});
    await waitFor(()=>expect(get).toHaveBeenCalledWith('/talents/question-jobs?person_ids=12&person_ids=13&mode=statistics'));
    await act(()=>result.current.submit({question:'记录统计',criteria:[{field:'city',operator:'eq',value:'北京'}]}));
    const first=post.mock.calls[0][1].idempotency_key;
    await act(()=>result.current.submit({question:'记录统计',criteria:[{field:'city',operator:'eq',value:'上海'}]}));
    expect(post.mock.calls[1][1].idempotency_key).not.toBe(first);
    rerender({mode:'compare'});
    await waitFor(()=>expect(get).toHaveBeenLastCalledWith('/talents/question-jobs?person_ids=12&person_ids=13&mode=compare'));
  });
  it('断线重复提交复用同一幂等键，修改问题才换键',async()=>{
    post.mockRejectedValue(new Error('offline'));
    const {result}=renderHook(()=>useQuestionJob([12]));
    await act(()=>result.current.submit({question:'技能？'}));
    const first=post.mock.calls[0][1].idempotency_key;
    await act(()=>result.current.submit({question:'技能？'}));
    expect(post.mock.calls[1][1].idempotency_key).toBe(first);
    await act(()=>result.current.submit({question:'项目？'}));
    expect(post.mock.calls[2][1].idempotency_key).not.toBe(first);
  });
  it('刷新后恢复服务端任务，重连只查询原任务',async()=>{
    get.mockResolvedValueOnce(pending).mockResolvedValueOnce({...pending,status:'completed'});
    const {result}=renderHook(()=>useQuestionJob([12]));
    await waitFor(()=>expect(result.current.job?.job_id).toBe(7));
    await act(async()=>result.current.resume());
    await waitFor(()=>expect(result.current.job?.status).toBe('completed'));
    expect(get).toHaveBeenLastCalledWith('/talents/question-jobs/7');expect(post).not.toHaveBeenCalled();
  });
  it('切人后丢弃旧提交的迟到响应',async()=>{
    let resolve!:(value:unknown)=>void;
    post.mockImplementation(()=>new Promise(done=>{resolve=done;}));
    const {result,rerender}=renderHook(({id})=>useQuestionJob([id]),{initialProps:{id:12}});
    let submission!:Promise<void>;
    act(()=>{submission=result.current.submit({question:'技能？'});});
    rerender({id:13});
    await act(async()=>{resolve(pending);await submission;});
    expect(result.current.job).toBeNull();
  });
  it('取消后清除轮询，保持取消终态',async()=>{
    get.mockResolvedValueOnce(pending);post.mockResolvedValue({status:'cancelled'});
    const {result}=renderHook(()=>useQuestionJob([12]));
    await waitFor(()=>expect(result.current.job?.job_id).toBe(7));
    await act(()=>result.current.cancel());
    expect(result.current.job?.status).toBe('cancelled');expect(result.current.busy).toBe(false);
  });
  it('结果读取被撤权后清除已展示的任务数据',async()=>{
    get.mockResolvedValueOnce(pending).mockRejectedValueOnce({code:403});
    const {result}=renderHook(()=>useQuestionJob([12]));
    await waitFor(()=>expect(result.current.job).not.toBeNull());
    await act(async()=>result.current.resume());
    await waitFor(()=>expect(result.current.error).toBe('连接失败'));
    expect(result.current.job).toBeNull();
  });
  it('取消恰逢完成时查询完整结果',async()=>{
    get.mockResolvedValueOnce(pending).mockResolvedValueOnce({...pending,status:'completed',processed:1});
    post.mockResolvedValue({status:'completed'});
    const {result}=renderHook(()=>useQuestionJob([12]));
    await waitFor(()=>expect(result.current.job?.status).toBe('running'));
    await act(()=>result.current.cancel());
    expect(result.current.job?.processed).toBe(1);
    expect(get).toHaveBeenLastCalledWith('/talents/question-jobs/7');
  });
});
