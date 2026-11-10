import {fireEvent,render,screen,waitFor} from '@testing-library/react';
import {describe,it,expect,vi} from 'vitest';
const {post}=vi.hoisted(()=>({post:vi.fn()}));
vi.mock('@/lib/http',()=>({post,getErrorMessage:()=> '请求失败'}));
import ProfileQuestion from './ProfileQuestion';

describe('档案问答',()=>{
  it('管理入口刷新索引后显示实际版本和片段数',async()=>{
    post.mockResolvedValue({profile_version:4,chunks:12});
    render(<ProfileQuestion personId={12} allowIndex/>);
    fireEvent.click(screen.getByRole('button',{name:'刷新全文索引'}));
    expect(await screen.findByText('索引就绪：档案版本 4，12 个片段。')).toBeTruthy();
    expect(post).toHaveBeenCalledWith('/talents/12/questions/index');
  });
  it('空问题不可提交',()=>{render(<ProfileQuestion personId={12}/>);expect((screen.getByRole('button',{name:'提 问'}) as HTMLButtonElement).disabled).toBe(true);});
  it('按person请求并显示版本与原文引用',async()=>{
    post.mockResolvedValue({status:'answered',answer:'熟悉 Python',profile_version:3,citations:[{citation_id:1,file_id:8,filename:'合成.docx',page_no:null,snippet:'Python项目'}]});
    render(<ProfileQuestion personId={12}/>);
    fireEvent.change(screen.getByLabelText('档案问题'),{target:{value:'技能？'}});
    fireEvent.click(screen.getByRole('button',{name:'提 问'}));
    await waitFor(()=>expect(post).toHaveBeenCalledWith('/talents/12/questions',{question:'技能？'}));
    expect(await screen.findByText('档案版本 3')).toBeTruthy();
    expect(screen.getByText('Python项目')).toBeTruthy();
  });
});
