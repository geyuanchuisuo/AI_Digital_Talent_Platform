import {render, screen, waitFor, fireEvent} from '@testing-library/react';
import {beforeEach, expect, it, vi} from 'vitest';
import PersonalProfile from './PersonalProfile';
import {setAuth, clearAuth} from '@/lib/auth';

const api = vi.hoisted(() => ({get: vi.fn(), downloadFile: vi.fn()}));
vi.mock('@/lib/http', () => ({...api, getErrorMessage: (e: Error) => e.message}));

beforeEach(() => { clearAuth(); api.get.mockReset(); api.downloadFile.mockReset(); });

it('shows only the signed-in persons profile and supports its source download', async () => {
  setAuth('test', {id: 2, username:'personal', role:'personal', person_id:129});
  api.get.mockResolvedValue({profile:{name:'Synthetic Person',city:'Test city',skills:['Python']},
    projects:[{name:'Synthetic project'}], resume_files:[{resume_file_id:134,filename:'source.docx'}],
    screen_history:[{effective_verdict:'INTERNAL ONLY'}]});
  render(<PersonalProfile />);
  expect(await screen.findByText('Synthetic Person')).toBeTruthy();
  expect(api.get.mock.calls.map(c => c[0])).toEqual(['/talents/129']);
  expect(screen.queryByText('INTERNAL ONLY')).toBeNull();
  expect(screen.queryByRole('button',{name:/删除|编辑|查重/})).toBeNull();
  fireEvent.click(screen.getByRole('button',{name:/下载原件/}));
  await waitFor(() => expect(api.downloadFile).toHaveBeenCalledWith('/talents/129/resume-file?resume_file_id=134','source.docx'));
});

it('does not query any profile for an unbound account', async () => {
  setAuth('test',{id:2,username:'personal',role:'personal',person_id:null});
  render(<PersonalProfile />);
  expect(await screen.findByText(/尚未绑定人才档案/)).toBeTruthy();
  expect(api.get).not.toHaveBeenCalled();
});
