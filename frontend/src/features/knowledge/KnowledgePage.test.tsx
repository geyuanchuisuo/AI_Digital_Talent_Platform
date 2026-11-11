import {fireEvent,render,screen,waitFor} from '@testing-library/react';
import {beforeEach,describe,expect,it,vi} from 'vitest';
const api=vi.hoisted(()=>({get:vi.fn(),post:vi.fn(),patch:vi.fn(),del:vi.fn(),downloadFile:vi.fn()}));
vi.mock('@/lib/http',()=>({...api,getErrorMessage:()=> '请求失败'}));
import KnowledgePage from './KnowledgePage';
const doc={id:1,title:'合成请假制度',access_scope:'public',enabled:true,expires_at:null,deleted_at:null,published_revision_id:2,
  versions:[{id:2,version:1,filename:'policy.txt',status:'completed',stage:'completed'}]};
beforeEach(()=>{Object.values(api).forEach(mock=>mock.mockReset());api.post.mockResolvedValue({});
  api.get.mockImplementation((path:string)=>Promise.resolve(path.startsWith('/knowledge/documents')?{items:[doc],total:1,can_manage:true}:null));});

describe('知识库页面',()=>{
  it('权限由服务端决定，个人页面没有管理动作',async()=>{
    render(<KnowledgePage readOnly/>);
    expect(await screen.findByText('合成请假制度')).toBeTruthy();
    expect(screen.queryByText('新建制度文档')).toBeNull();expect(screen.queryByText('删除文档')).toBeNull();
    fireEvent.click(screen.getByRole('button',{name:'下载原件'}));
    await waitFor(()=>expect(api.downloadFile).toHaveBeenCalledWith('/knowledge/versions/2/source','policy.txt'));
  });
  it('服务端未授予管理权限时不显示管理表单',async()=>{
    api.get.mockImplementation((path:string)=>Promise.resolve(path.startsWith('/knowledge/documents')?{items:[doc],total:1,can_manage:false}:null));
    render(<KnowledgePage/>);await screen.findByText('合成请假制度');
    expect(screen.queryByLabelText('文档标题')).toBeNull();expect(screen.queryByText('删除文档')).toBeNull();
  });
  it('新建文档默认受限，显式传递可选到期时间',async()=>{
    render(<KnowledgePage/>);
    const title=await screen.findByLabelText('文档标题');fireEvent.change(title,{target:{value:'合成新制度'}});
    fireEvent.click(screen.getByRole('button',{name:'新建文档'}));
    await waitFor(()=>expect(api.post).toHaveBeenCalledWith('/knowledge/documents',{
      title:'合成新制度',access_scope:'admin',expires_at:null}));
  });
  it('版本上传沿用multipart而不传文件路径',async()=>{
    render(<KnowledgePage/>);const input=await screen.findByLabelText('上传版本：合成请假制度');
    const file=new File(['合成正文'],'policy.txt',{type:'text/plain'});
    fireEvent.change(input,{target:{files:[file]}});
    await waitFor(()=>expect(api.post).toHaveBeenCalledWith('/knowledge/documents/1/versions',expect.any(FormData)));
    expect((api.post.mock.calls[0][1] as FormData).get('file')).toBe(file);
  });
});
