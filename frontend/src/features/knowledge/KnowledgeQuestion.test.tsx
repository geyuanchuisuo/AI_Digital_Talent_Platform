import {fireEvent,render,screen,waitFor} from '@testing-library/react';
import {beforeEach,describe,expect,it,vi} from 'vitest';
const api=vi.hoisted(()=>({get:vi.fn(),post:vi.fn(),downloadFile:vi.fn()}));
vi.mock('@/lib/http',()=>({...api,getErrorMessage:()=> '连接失败'}));
import KnowledgeQuestion from './KnowledgeQuestion';
const completed={job_id:3,question:'如何请假？',status:'completed',result:{status:'answered',answer:'需要审批。',
  citations:[{citation_id:1,document_id:1,revision_id:2,version:1,document_title:'合成制度',filename:'policy.txt',page_no:null,
    snippet:'请假需审批',offset_start:0,offset_end:5}]}};
beforeEach(()=>{api.get.mockReset();api.post.mockReset();api.downloadFile.mockReset();api.get.mockResolvedValue(null);});

describe('制度问答',()=>{
  it('恢复本人最近任务并展示可核对的版本和原文',async()=>{
    api.get.mockResolvedValueOnce(completed);render(<KnowledgeQuestion/>);
    expect(await screen.findByText('需要审批。')).toBeTruthy();expect(screen.getByText(/版本 1/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button',{name:'下载引用原件'}));
    await waitFor(()=>expect(api.downloadFile).toHaveBeenCalledWith('/knowledge/versions/2/source','policy.txt'));
  });
  it('断线重试复用幂等键',async()=>{
    api.post.mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(completed);
    render(<KnowledgeQuestion/>);fireEvent.change(screen.getByLabelText('制度问题'),{target:{value:'如何请假？'}});
    fireEvent.click(screen.getByRole('button',{name:'查询制度'}));await screen.findByText('连接失败');
    fireEvent.click(screen.getByRole('button',{name:'查询制度'}));await screen.findByText('需要审批。');
    expect(api.post.mock.calls[0][1].idempotency_key).toBe(api.post.mock.calls[1][1].idempotency_key);
  });
  it('文档版本或授权失效后隐藏旧回答',async()=>{
    api.get.mockResolvedValueOnce(completed).mockResolvedValueOnce({...completed,status:'source_unavailable',result:null});
    render(<KnowledgeQuestion/>);await screen.findByText('需要审批。');fireEvent.click(screen.getByRole('button',{name:'刷新问答'}));
    await screen.findByText(/旧回答已隐藏/);expect(screen.queryByText('需要审批。')).toBeNull();
  });
  it('运行中任务可以取消，不显示未完成回答',async()=>{
    api.get.mockResolvedValueOnce({...completed,status:'running',result:null});
    api.post.mockResolvedValue({...completed,status:'cancelled',result:null});render(<KnowledgeQuestion/>);
    fireEvent.click(await screen.findByRole('button',{name:'取消问答'}));
    expect(await screen.findByText('问答已取消')).toBeTruthy();expect(screen.queryByText('需要审批。')).toBeNull();
  });
});
