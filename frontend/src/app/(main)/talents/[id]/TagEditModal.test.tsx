import {fireEvent,render,screen,waitFor} from '@testing-library/react';
import {describe,it,expect,vi} from 'vitest';
import type {TalentProfileOut} from '../types';
const {patchTalent}=vi.hoisted(()=>({patchTalent:vi.fn()}));
vi.mock('../api',()=>({patchTalent}));
vi.mock('@/features/talents/TagSuggestions',()=>({default:({onChoose}:{onChoose:(name:string,kind:string,proof:unknown)=>void})=>
  <button onClick={()=>onChoose('Go','skill',{code:'skill:Go',candidate_id:'a'.repeat(64),profile_version:3,
    catalog_version:'talent-tags-v1',reason:'原文已核对'})}>加入来源确认样例</button>}));
import TagEditModal from './TagEditModal';

describe('人工技能标签',()=>{
  it('建议仅加入草稿，最终保存才提交来源确认',async()=>{
    patchTalent.mockClear();patchTalent.mockResolvedValue({vector_status:'synced'});
    const profile={id:12,tags:[],skills:[]} as unknown as TalentProfileOut;
    render(<TagEditModal open profile={profile} onClose={vi.fn()} onPatched={vi.fn()}/>);
    fireEvent.click(screen.getByRole('button',{name:'加入来源确认样例'}));
    expect(patchTalent).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button',{name:'保 存'}));
    await waitFor(()=>expect(patchTalent).toHaveBeenCalledWith(12,{tags:[{tag:'Go',tag_type:'skill'}],
      tag_proofs:[{code:'skill:Go',candidate_id:'a'.repeat(64),profile_version:3,catalog_version:'talent-tags-v1',reason:'原文已核对'}]}));
  });
  it('重新打开后仍可移除独立添加的人工技能标签',async()=>{
    const profile={id:12,tags:[{tag:'Python',tag_type:'skill',source:'manual'}],skills:[]} as unknown as TalentProfileOut;
    patchTalent.mockResolvedValue({vector_status:'synced'});
    render(<TagEditModal open profile={profile} onClose={vi.fn()} onPatched={vi.fn()}/>);
    fireEvent.click(await screen.findByLabelText('删除标签 Python'));
    fireEvent.click(screen.getByRole('button',{name:'保 存'}));
    await waitFor(()=>expect(patchTalent).toHaveBeenCalledWith(12,{tags:[]}));
  });
  it('实际来自技能字段的标签继续通过技能字段管理',async()=>{
    const profile={id:12,tags:[{tag:'Python',tag_type:'skill',source:'manual'}],skills:['Python']} as unknown as TalentProfileOut;
    render(<TagEditModal open profile={profile} onClose={vi.fn()} onPatched={vi.fn()}/>);
    expect(await screen.findByText('Python')).toBeTruthy();
    expect(screen.queryByLabelText('删除标签 Python')).toBeNull();
  });
});
