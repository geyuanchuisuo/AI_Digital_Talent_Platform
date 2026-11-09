import {fireEvent,render,screen} from '@testing-library/react';
import {describe,it,expect,vi} from 'vitest';
const {post}=vi.hoisted(()=>({post:vi.fn()}));
vi.mock('@/lib/http',()=>({post,getErrorMessage:()=> '失败'}));
import BatchProfileQuestion from './BatchProfileQuestion';
describe('批量问答',()=>{
  it('逐人标识不足与失败并展示覆盖数',async()=>{
    post.mockResolvedValue({requested:2,answered:0,items:[{person_id:1,status:'insufficient_evidence',answer:'资料不足'},
      {person_id:2,status:'failed',error:'不可用'}]});
    render(<BatchProfileQuestion people={[{id:1,name:'甲'},{id:2,name:'乙'}]}/>);
    fireEvent.change(screen.getByLabelText('批量档案问题'),{target:{value:'技能？'}});
    fireEvent.click(screen.getByRole('button',{name:'逐人提问'}));
    expect(await screen.findByText('请求 2 人，已回答 0 人')).toBeTruthy();
    expect(screen.getByText(/乙 · 处理失败/)).toBeTruthy();
    expect(post).toHaveBeenCalledWith('/talents/questions/batch',{person_ids:[1,2],question:'技能？'});
  });
});
