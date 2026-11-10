import {fireEvent,render,screen,waitFor} from '@testing-library/react';
import {describe,it,expect,vi} from 'vitest';
import type {TalentProfileOut} from '../types';
const {patchTalent}=vi.hoisted(()=>({patchTalent:vi.fn()}));
vi.mock('../api',()=>({patchTalent}));
vi.mock('@/features/talents/TagSuggestions',()=>({default:()=>null}));
import TagEditModal from './TagEditModal';

describe('人工技能标签',()=>{
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
