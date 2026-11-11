import {fireEvent,render,screen,waitFor} from '@testing-library/react';
import {describe,it,expect,vi} from 'vitest';
const {get}=vi.hoisted(()=>({get:vi.fn()}));
vi.mock('@/lib/http',()=>({get,getErrorMessage:()=> '请求失败'}));
import TagSuggestions from './TagSuggestions';

describe('有原文依据的标签建议',()=>{
  it('服务器拒绝当前访问时清除已展示的来源',async()=>{
    get.mockResolvedValueOnce({profile_version:4,items:[{tag:'go',evidence_status:'reviewed_source',
      evidence:[{file_id:7,page_no:2,snippet:'旧的受限片段'}]}]}).mockRejectedValueOnce({code:403});
    render(<TagSuggestions personId={12} onChoose={vi.fn()}/>);
    fireEvent.click(screen.getByRole('button',{name:'查看已保存标签依据'}));
    expect(await screen.findByText('旧的受限片段')).toBeTruthy();
    fireEvent.click(screen.getByRole('button',{name:'查看已保存标签依据'}));
    await waitFor(()=>expect(screen.queryByText('旧的受限片段')).toBeNull());
  });
  it('特殊语境须填写原因，选用时携带服务器候选标识而不是伪造原文',async()=>{
    const id='a'.repeat(64);
    get.mockResolvedValue({profile_version:3,catalog_version:'talent-tags-v1',items:[{
      code:'skill:Go',name:'Go',type:'skill',candidate_id:id,context_status:'aspirational',
      file_id:7,page_no:2,snippet:'计划学习Go',evidence:[{file_id:7,page_no:2,snippet:'计划学习Go',quality_status:'ready'}]}]});
    const choose=vi.fn();render(<TagSuggestions personId={12} onChoose={choose}/>);
    fireEvent.click(screen.getByRole('button',{name:'查看原文标签建议'}));
    expect(await screen.findByText('学习／意向语境')).toBeTruthy();
    expect((screen.getByRole('button',{name:'选用 Go'}) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText('采用Go的原因'),{target:{value:'仅记录学习意向'}});
    fireEvent.click(screen.getByRole('button',{name:'选用 Go'}));
    expect(choose).toHaveBeenCalledWith('Go','skill',{code:'skill:Go',candidate_id:id,profile_version:3,
      catalog_version:'talent-tags-v1',reason:'仅记录学习意向'});
  });
  it('展示已保存标签的依据状态并明确不是能力评级',async()=>{
    get.mockResolvedValue({profile_version:4,items:[{tag:'go',evidence_status:'reviewed_source',
      source_profile_version:3,evidence:[{file_id:7,page_no:2,snippet:'使用Go开发服务'}]}]});
    render(<TagSuggestions personId={12} onChoose={vi.fn()}/>);
    fireEvent.click(screen.getByRole('button',{name:'查看已保存标签依据'}));
    expect(await screen.findByText('已人工核对原文')).toBeTruthy();
    expect(screen.getByText(/不是正式测评或能力评级/)).toBeTruthy();
    expect(screen.getByText('使用Go开发服务')).toBeTruthy();
    expect(get).toHaveBeenCalledWith('/talents/12/tag-quality');
  });
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
