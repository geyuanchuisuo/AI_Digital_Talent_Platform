import {render, screen, fireEvent, waitFor} from '@testing-library/react';
import {beforeEach, expect, it, vi} from 'vitest';
import SourceEvidence from './SourceEvidence';

const api = vi.hoisted(() => ({get:vi.fn(), downloadFile:vi.fn()}));
vi.mock('@/lib/http', () => ({...api, getErrorMessage:(error:Error) => error.message}));
beforeEach(() => {api.get.mockReset(); api.downloadFile.mockReset();});

it('shows version, original quotation, actual page and OCR review status', async () => {
  api.get.mockResolvedValue({items:[{person_id:7,version:2,status:'ready',evidence:[{
    field_path:'skills',file_id:12,filename:'合成.pdf',page_no:3,locator_type:'page',
    snippet:'熟悉 Python 与数据分析',source:'ocr',confidence:0.72,review_status:'pending_review',
  }]}]});
  render(<SourceEvidence personId={7} />);
  expect(await screen.findByText('熟悉 Python 与数据分析')).toBeTruthy();
  expect(screen.getByText('第 3 页')).toBeTruthy();
  expect(screen.getByText('待核实')).toBeTruthy();
  fireEvent.click(screen.getByRole('button',{name:'下载依据原件'}));
  await waitFor(() => expect(api.downloadFile).toHaveBeenCalledWith('/talents/7/resume-file?resume_file_id=12','合成.pdf'));
});

it('labels DOCX as document text and never invents page one', async () => {
  api.get.mockResolvedValue({items:[{version:1,evidence:[{field_path:'basic.name',file_id:1,
    filename:'合成.docx',page_no:null,locator_type:'document_text',snippet:'张晓林',source:'text',review_status:'pending_review'}]}]});
  render(<SourceEvidence personId={7} />);
  expect(await screen.findByText('文档文本（无固定页码）')).toBeTruthy();
  expect(screen.queryByText('第 1 页')).toBeNull();
});

it('does not fabricate citations for an older profile without page evidence', async () => {
  api.get.mockResolvedValue({items:[{version:1,evidence:[],evidence_status:'unavailable'}]});
  render(<SourceEvidence personId={7} />);
  expect(await screen.findByText('暂无可核对的原文证据')).toBeTruthy();
});

it('shows source access failures instead of an empty successful result', async () => {
  api.get.mockRejectedValue(new Error('无权访问该资源'));
  render(<SourceEvidence personId={8} />);
  expect(await screen.findByText('无权访问该资源')).toBeTruthy();
});
