import {fireEvent,render,screen} from '@testing-library/react';
import {beforeEach,describe,it,expect,vi} from 'vitest';
const {post,get}=vi.hoisted(()=>({post:vi.fn(),get:vi.fn()}));
vi.mock('@/lib/http',()=>({post,get,getErrorMessage:()=> '失败'}));
import BatchProfileQuestion from './BatchProfileQuestion';
beforeEach(()=>{post.mockReset();get.mockReset();get.mockResolvedValue(null);sessionStorage.clear();});
describe('批量问答',()=>{
  it('逐人标识不足与失败并展示覆盖数',async()=>{
    post.mockResolvedValue({job_id:1,status:'completed',question:'技能？',processed:2,requested:2,answered:0,items:[{person_id:1,status:'insufficient_evidence',answer:'资料不足'},
      {person_id:2,status:'failed',error:'不可用'}]});
    render(<BatchProfileQuestion people={[{id:1,name:'甲'},{id:2,name:'乙'}]}/>);
    fireEvent.change(screen.getByLabelText('批量档案问题'),{target:{value:'技能？'}});
    fireEvent.click(screen.getByRole('button',{name:'逐人提问'}));
    expect(await screen.findByText('请求 2 人，已回答 0 人')).toBeTruthy();
    expect(screen.getByText(/乙 · 处理失败/)).toBeTruthy();
    expect(post).toHaveBeenCalledWith('/talents/question-jobs',expect.objectContaining({person_ids:[1,2],question:'技能？'}));
  });
  it('明确条件统计不要求自然语言问题，展示待核实人数及统计范围',async()=>{
    post.mockResolvedValue({job_id:2,status:'completed',mode:'statistics',processed:2,requested:2,answered:0,items:[],
      criteria:[{field:'skills',operator:'contains',value:'java'}],analysis:{population:2,exact:false,
        matched_count:1,not_matched_count:0,unknown_count:1,matched_ids:[1],not_matched_ids:[],unknown_ids:[2],
        coverage_incomplete:true,basis:'字段记录',computed_at:'2026-11-11T06:00:00Z',rows:[]}});
    render(<BatchProfileQuestion people={[{id:1,name:'同名'},{id:2,name:'同名'}]}/>);
    fireEvent.click(screen.getByRole('radio',{name:'档案字段统计'}));
    fireEvent.change(screen.getByLabelText('条件1的值'),{target:{value:'Java'}});
    fireEvent.click(screen.getByRole('button',{name:'统计所选档案'}));
    expect(await screen.findByText(/已知匹配 1 人.*待核实 1 人/)).toBeTruthy();
    expect(screen.getByText(/覆盖不完整/)).toBeTruthy();
    expect(screen.getByText(/同名 · ID 2/)).toBeTruthy();
    expect(post).toHaveBeenCalledWith('/talents/question-jobs',expect.objectContaining({mode:'statistics',
      criteria:[{field:'skills',operator:'contains',value:'Java'}],person_ids:[1,2]}));
  });
  it('比较表按ID区分同名对象，标出字段版本依据',async()=>{
    post.mockResolvedValue({job_id:3,status:'completed',mode:'compare',processed:2,requested:2,answered:0,items:[],
      analysis:{population:2,criteria:[],exact:false,coverage_incomplete:true,rows:[
        {person_id:1,profile_version:4,fields:{skills:{value:['Java']},city:{value:'甲城'}},status:'unknown'},
        {person_id:2,profile_version:5,fields:{skills:{value:['Go']},city:{value:'乙城'}},status:'unknown'}]}});
    render(<BatchProfileQuestion people={[{id:1,name:'同名'},{id:2,name:'同名'}]}/>);
    fireEvent.click(screen.getByRole('radio',{name:'字段与证据比较'}));
    fireEvent.click(screen.getByRole('button',{name:'删除条件1'}));
    fireEvent.change(screen.getByLabelText('批量档案问题'),{target:{value:'比较项目经历'}});
    fireEvent.click(screen.getByRole('button',{name:'比较所选档案'}));
    expect(await screen.findByText('同名 · ID 1 · 版本 4')).toBeTruthy();
    expect(screen.getByText('同名 · ID 2 · 版本 5')).toBeTruthy();
    expect(screen.getByText('甲城')).toBeTruthy();expect(screen.getByText('乙城')).toBeTruthy();
    expect(screen.getByText(/表格依据为固定版本的档案字段/)).toBeTruthy();
    expect(screen.getByText(/覆盖不完整/)).toBeTruthy();
  });
  it('比较不能静默丢弃空条件，须填写或明确删除',()=>{
    render(<BatchProfileQuestion people={[{id:1,name:'甲'},{id:2,name:'乙'}]}/>);
    fireEvent.click(screen.getByRole('radio',{name:'字段与证据比较'}));
    fireEvent.change(screen.getByLabelText('批量档案问题'),{target:{value:'比较技能'}});
    expect((screen.getByRole('button',{name:'比较所选档案'}) as HTMLButtonElement).disabled).toBe(true);
  });
  it('刷新恢复统计模式及服务器提交条件，不将编辑框草稿当任务条件',async()=>{
    sessionStorage.setItem('b08-mode:1,2','statistics');
    get.mockImplementation(async (url:string)=>url.includes('mode=statistics')?{
      job_id:8,status:'completed',mode:'statistics',processed:2,requested:2,answered:0,items:[],
      criteria:[{field:'skills',operator:'contains',value:'java'}]}:null);
    render(<BatchProfileQuestion people={[{id:1,name:'甲'},{id:2,name:'乙'}]}/>);
    expect(await screen.findByText('任务条件（同时满足）：技能 包含完整技能词 java')).toBeTruthy();
    expect((screen.getByRole('radio',{name:'档案字段统计'}) as HTMLInputElement).checked).toBe(true);
    expect(get).toHaveBeenCalledWith('/talents/question-jobs?person_ids=1&person_ids=2&mode=statistics');
  });
});
