import {fireEvent,render,screen,waitFor} from '@testing-library/react';
import {describe,it,expect,vi} from 'vitest';
const {post,get}=vi.hoisted(()=>({post:vi.fn(),get:vi.fn()}));
vi.mock('@/lib/http',()=>({post,get:(path:string)=>path.startsWith('/talents/question-jobs')?Promise.resolve(null):get(path),getErrorMessage:()=> '请求失败'}));
import ProfileQuestion from './ProfileQuestion';
const completed=(item:Record<string,unknown>)=>({job_id:10,status:'completed',question:'技能？',items:[item],requested:1,processed:1,answered:1});

describe('档案问答',()=>{
  it('切换人员后不携带上一人的追问关联',async()=>{
    post.mockResolvedValue(completed({question_id:61,status:'answered',answer:'切人前回答',profile_version:3,citations:[]}));
    const view=render(<ProfileQuestion personId={12}/>);
    fireEvent.change(screen.getByLabelText('档案问题'),{target:{value:'技能？'}});
    fireEvent.click(screen.getByRole('button',{name:'提 问'}));
    expect(await screen.findByText('切人前回答')).toBeTruthy();
    fireEvent.click(screen.getByRole('button',{name:'继续追问'}));
    view.rerender(<ProfileQuestion personId={13}/>);
    expect(screen.queryByText(/追问档案版本/)).toBeNull();
    fireEvent.change(screen.getByLabelText('档案问题'),{target:{value:'新人的技能？'}});
    fireEvent.click(screen.getByRole('button',{name:'提 问'}));
    await waitFor(()=>expect(post).toHaveBeenLastCalledWith('/talents/question-jobs',expect.objectContaining({person_ids:[13],question:'新人的技能？'})));
  });
  it('追问失败后保留父记录以便原请求重试',async()=>{
    post.mockResolvedValueOnce(completed({question_id:71,status:'answered',answer:'重试前回答',profile_version:3,citations:[]}))
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce(completed({question_id:72,status:'answered',answer:'重试成功',profile_version:3,citations:[]}));
    render(<ProfileQuestion personId={12}/>);
    fireEvent.change(screen.getByLabelText('档案问题'),{target:{value:'技能？'}});
    fireEvent.click(screen.getByRole('button',{name:'提 问'}));
    expect(await screen.findByText('重试前回答')).toBeTruthy();
    fireEvent.click(screen.getByRole('button',{name:'继续追问'}));
    fireEvent.change(screen.getByLabelText('档案问题'),{target:{value:'依据？'}});
    fireEvent.click(screen.getByRole('button',{name:'提 问'}));
    expect(await screen.findByText('请求失败')).toBeTruthy();
    expect(screen.getByText(/追问档案版本 3/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button',{name:'提 问'}));
    expect(await screen.findByText('重试成功')).toBeTruthy();
    expect(post).toHaveBeenLastCalledWith('/talents/question-jobs',expect.objectContaining({person_ids:[12],question:'依据？',follow_up_to:71,version:3}));
  });
  it('继续追问固定上一轮版本，新问题清除关联',async()=>{
    post.mockResolvedValueOnce(completed({question_id:41,status:'answered',answer:'第一轮依据',profile_version:3,citations:[]}))
      .mockResolvedValueOnce(completed({question_id:42,status:'answered',answer:'追问依据',profile_version:3,citations:[]}))
      .mockResolvedValueOnce(completed({question_id:43,status:'answered',answer:'最新版本',profile_version:4,citations:[]}));
    render(<ProfileQuestion personId={12}/>);
    fireEvent.change(screen.getByLabelText('档案问题'),{target:{value:'技能？'}});
    fireEvent.click(screen.getByRole('button',{name:'提 问'}));
    expect(await screen.findByText('第一轮依据')).toBeTruthy();
    fireEvent.click(screen.getByRole('button',{name:'继续追问'}));
    expect(screen.getByText(/追问档案版本 3/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText('档案问题'),{target:{value:'这项的依据？'}});
    fireEvent.click(screen.getByRole('button',{name:'提 问'}));
    await waitFor(()=>expect(post).toHaveBeenLastCalledWith('/talents/question-jobs',expect.objectContaining({person_ids:[12],
      question:'这项的依据？',follow_up_to:41,version:3})));
    expect(await screen.findByText('追问依据')).toBeTruthy();
    fireEvent.click(screen.getByRole('button',{name:'新问题'}));
    fireEvent.change(screen.getByLabelText('档案问题'),{target:{value:'当前资料？'}});
    fireEvent.click(screen.getByRole('button',{name:'提 问'}));
    await waitFor(()=>expect(post).toHaveBeenLastCalledWith('/talents/question-jobs',expect.objectContaining({person_ids:[12],question:'当前资料？'})));
  });
  it('可以从当前账号的历史记录继续追问',async()=>{
    get.mockResolvedValue({total:1,items:[{question_id:51,question:'旧问题',status:'answered',
      answer:'历史事实',profile_version:2,citations:[]}]});
    post.mockResolvedValue(completed({question_id:52,status:'answered',answer:'跟进事实',profile_version:2,citations:[]}));
    render(<ProfileQuestion personId={12}/>);
    fireEvent.click(screen.getByRole('button',{name:'查看问答历史'}));
    fireEvent.click(await screen.findByRole('button',{name:'追问此记录'}));
    fireEvent.change(screen.getByLabelText('档案问题'),{target:{value:'进一步说明？'}});
    fireEvent.click(screen.getByRole('button',{name:'提 问'}));
    await waitFor(()=>expect(post).toHaveBeenLastCalledWith('/talents/question-jobs',expect.objectContaining({person_ids:[12],
      question:'进一步说明？',follow_up_to:51,version:2})));
  });
  it('按本人档案加载历史并显示旧版本及原文引用',async()=>{
    get.mockResolvedValue({total:1,items:[{question_id:2,question:'旧问题',status:'answered',
      answer:'历史事实',profile_version:2,citations:[{citation_id:1,file_id:8,page_no:1,snippet:'旧原文'}]}]});
    render(<ProfileQuestion personId={12}/>);
    fireEvent.click(screen.getByRole('button',{name:'查看问答历史'}));
    expect(await screen.findByText('旧问题')).toBeTruthy();
    expect(screen.getByText('历史事实')).toBeTruthy();
    expect(screen.getByText('旧原文')).toBeTruthy();
    expect(get).toHaveBeenCalledWith('/talents/12/questions?offset=0&limit=10');
  });
  it('切换人员时丢弃旧人员迟到的历史响应',async()=>{
    let finish:(value:unknown)=>void=()=>{};
    get.mockReturnValue(new Promise(resolve=>{finish=resolve;}));
    const view=render(<ProfileQuestion personId={12}/>);
    fireEvent.click(screen.getByRole('button',{name:'查看问答历史'}));
    view.rerender(<ProfileQuestion personId={13}/>);
    finish({total:1,items:[{question_id:2,question:'其他人秘密',answer:'秘密',profile_version:2,citations:[]}]});
    await waitFor(()=>expect(screen.queryByText('其他人秘密')).toBeNull());
  });
  it('管理入口刷新索引后显示实际版本和片段数',async()=>{
    post.mockResolvedValue({profile_version:4,chunks:12});
    render(<ProfileQuestion personId={12} allowIndex/>);
    fireEvent.click(screen.getByRole('button',{name:'刷新全文索引'}));
    expect(await screen.findByText('索引就绪：档案版本 4，12 个片段。')).toBeTruthy();
    expect(post).toHaveBeenCalledWith('/talents/12/questions/index');
  });
  it('空问题不可提交',()=>{render(<ProfileQuestion personId={12}/>);expect((screen.getByRole('button',{name:'提 问'}) as HTMLButtonElement).disabled).toBe(true);});
  it('按person请求并显示版本与原文引用',async()=>{
    post.mockResolvedValue(completed({status:'answered',answer:'熟悉 Python',profile_version:3,citations:[{citation_id:1,file_id:8,filename:'合成.docx',page_no:null,snippet:'Python项目'}]}));
    render(<ProfileQuestion personId={12}/>);
    fireEvent.change(screen.getByLabelText('档案问题'),{target:{value:'技能？'}});
    fireEvent.click(screen.getByRole('button',{name:'提 问'}));
    await waitFor(()=>expect(post).toHaveBeenCalledWith('/talents/question-jobs',expect.objectContaining({person_ids:[12],question:'技能？'})));
    expect(await screen.findByText('档案版本 3')).toBeTruthy();
    expect(screen.getByText('Python项目')).toBeTruthy();
  });
});
