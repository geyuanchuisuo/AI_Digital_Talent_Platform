import {fireEvent,render,screen,waitFor} from '@testing-library/react';
import {describe,it,expect,vi} from 'vitest';
const {get}=vi.hoisted(()=>({get:vi.fn()}));
vi.mock('@/lib/http',()=>({get,getErrorMessage:()=> '请求失败'}));
import TagSuggestions from './TagSuggestions';

describe('有原文依据的标签建议',()=>{
  it('显示命中原文，只有用户选择才加入草稿',async()=>{
    get.mockResolvedValue({profile_version:3,catalog_version:'talent-tags-v1',items:[{
      code:'skill:Python',name:'Python',type:'skill',file_id:7,page_no:2,snippet:'原文：使用 Python',review_status:'pending_review'}]});
    const choose=vi.fn();
    render(<TagSuggestions personId={12} onChoose={choose}/>);
    fireEvent.click(screen.getByRole('button',{name:'查看原文标签建议'}));
    expect(await screen.findByText('原文：使用 Python')).toBeTruthy();
    expect(choose).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button',{name:'选用 Python'}));
    expect(choose).toHaveBeenCalledWith('Python','skill');
    expect(get).toHaveBeenCalledWith('/talents/12/tag-suggestions');
  });
  it('切人后不显示旧人的迟到依据',async()=>{
    let finish:(value:unknown)=>void=()=>{};
    get.mockReturnValue(new Promise(resolve=>{finish=resolve;}));
    const view=render(<TagSuggestions personId={12} onChoose={vi.fn()}/>);
    fireEvent.click(screen.getByRole('button',{name:'查看原文标签建议'}));
    view.rerender(<TagSuggestions personId={13} onChoose={vi.fn()}/>);
    finish({profile_version:3,items:[{code:'skill:Python',name:'Python',type:'skill',file_id:7,snippet:'旧人的资料'}]});
    await waitFor(()=>expect(screen.queryByText('旧人的资料')).toBeNull());
  });
});
