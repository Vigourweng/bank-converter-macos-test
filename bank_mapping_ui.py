"""Reusable column-mapping dialog for local text and Excel statements."""
import json
from pathlib import Path
import bank_formats as formats


def mapping_dialog(parent, paths, rules_path, on_saved):
    import tkinter as tk
    from tkinter import ttk, messagebox
    tables_paths=[p for p in paths if p.suffix.lower() in ('.csv','.tsv','.txt','.xls','.xlsx')]
    if not tables_paths:
        raise ValueError('列对应只适用于表格文件。OFX、MT940、camt 使用自身结构，不能随意映射。')
    window=tk.Toplevel(parent)
    window.title('设置列对应 / 保存为格式规则')
    window.geometry('960x760')
    body=ttk.Frame(window,padding=16);body.pack(fill='both',expand=True)
    body.columnconfigure(1,weight=1);body.rowconfigure(4,weight=1)
    chosen=tk.StringVar(value=str(tables_paths[0]))
    sheet=tk.StringVar();header=tk.StringVar(value='1');order=tk.StringVar(value='DMY')
    ttk.Label(body,text='文件').grid(row=0,column=0,sticky='w')
    file_box=ttk.Combobox(body,textvariable=chosen,values=[str(p) for p in tables_paths],state='readonly')
    file_box.grid(row=0,column=1,columnspan=3,sticky='ew',pady=5)
    ttk.Label(body,text='工作表').grid(row=1,column=0,sticky='w')
    sheet_box=ttk.Combobox(body,textvariable=sheet,state='readonly');sheet_box.grid(row=1,column=1,sticky='ew',pady=5)
    ttk.Label(body,text='表头所在行').grid(row=1,column=2,padx=8)
    ttk.Spinbox(body,from_=1,to=100,textvariable=header,width=7).grid(row=1,column=3,sticky='w')
    ttk.Label(body,text='日期顺序').grid(row=2,column=0,sticky='w')
    ttk.Combobox(body,textvariable=order,values=('DMY','MDY','AUTO'),state='readonly',width=9).grid(row=2,column=1,sticky='w')
    ttk.Label(body,text='DMY=日/月/年；MDY=月/日/年；AUTO=有歧义则提示').grid(row=3,column=0,columnspan=4,sticky='w',pady=5)
    preview=tk.Text(body,height=9,wrap='none');preview.grid(row=4,column=0,columnspan=4,sticky='nsew',pady=8)
    maps=ttk.Frame(body);maps.grid(row=5,column=0,columnspan=4,sticky='ew')
    variables={};boxes={};cache={'tables':[],'rows':[],'labels':[]}
    for i,(role,label) in enumerate(formats.FIELDS.items()):
        r,c=divmod(i,2);c*=2
        ttk.Label(maps,text=label).grid(row=r,column=c,sticky='w',padx=5,pady=4)
        variables[role]=tk.StringVar(value='不使用')
        boxes[role]=ttk.Combobox(maps,textvariable=variables[role],state='readonly',width=31)
        boxes[role].grid(row=r,column=c+1,sticky='ew',padx=5,pady=4)
    ttk.Label(body,text='必须选择日期，以及金额列或收入/支出列。规则按表头布局复用，不按本月文件名绑定。',wraplength=890).grid(row=6,column=0,columnspan=4,sticky='w',pady=10)
    buttons=ttk.Frame(body);buttons.grid(row=7,column=0,columnspan=4,sticky='ew')

    def refresh(*unused):
        try:
            rows=dict(cache['tables'])[sheet.get()]
            n=int(header.get())-1
            if n<0 or n>=len(rows):raise ValueError('表头行超出文件范围')
            labels=['不使用']+[f'{i+1}: {cell or "（空白表头）"}' for i,cell in enumerate(rows[n])]
            cache.update(rows=rows,labels=labels)
            try:inferred=formats.infer_columns(rows[n])
            except formats.FormatError:inferred={}
            profiles=formats.read_profiles(rules_path)
            profile=next((p for p in reversed(profiles) if p.get('signature')==formats.signature(rows[n]) and (not p.get('sheet') or p['sheet']==sheet.get())),None)
            if profile:inferred=profile['columns'];order.set(profile.get('date_order','DMY'))
            for role,box in boxes.items():
                box['values']=labels
                indices=inferred.get(role,[])
                if isinstance(indices,int):indices=[indices]
                variables[role].set(labels[indices[0]+1] if indices and indices[0]+1<len(labels) else '不使用')
            preview.configure(state='normal');preview.delete('1.0','end')
            for line,row in enumerate(rows[max(0,n-2):n+6],max(1,n-1)):
                preview.insert('end',f'第 {line} 行：'+ ' | '.join(f'[{i+1}] {v}' for i,v in enumerate(row))+'\n')
            preview.configure(state='disabled')
        except (ValueError,OSError) as exc:messagebox.showerror('无法读取表头',str(exc),parent=window)

    def load(*unused):
        try:
            cache['tables']=formats.load_tables(Path(chosen.get()))
            names=[name for name,rows in cache['tables']]
            sheet_box['values']=names
            sheet.set(names[0]);header.set('1')
            try:
                name,rows,n,columns,date_order,saved=formats.find_table(Path(chosen.get()),formats.read_profiles(rules_path))
                sheet.set(name);header.set(str(n+1))
                if date_order:order.set(date_order)
            except ValueError:pass
            refresh()
        except (OSError,ValueError) as exc:messagebox.showerror('不能设置此文件',str(exc),parent=window)

    def save():
        try:
            n=int(header.get())-1
            rows=dict(cache['tables'])[sheet.get()]
            columns={role:[cache['labels'].index(v.get())-1] for role,v in variables.items() if v.get()!='不使用'}
            if not formats.valid_columns(columns):raise ValueError('请选择日期列，以及金额或收入/支出列')
            used=[i for indices in columns.values() for i in indices]
            if len(set(used))!=len(used):raise ValueError('同一列不能同时映射到多个字段')
            rule={'name':Path(chosen.get()).stem,'signature':formats.signature(rows[n]),'columns':columns,
                  'sheet':sheet.get() if len(cache['tables'])>1 else '', 'date_order':order.get()}
            profiles=formats.read_profiles(rules_path)
            profiles=[p for p in profiles if not (p.get('signature')==rule['signature'] and p.get('sheet','')==rule['sheet'])]+[rule]
            # Test the rule before saving it for future files.
            entries,summary=formats.generic_statement(Path(chosen.get()),profiles,order.get())
            if not entries:raise ValueError('该列规则没有识别出交易，未保存')
            Path(rules_path).write_text(json.dumps({'version':1,'profiles':profiles},ensure_ascii=False,indent=2),encoding='utf-8')
            messagebox.showinfo('规则已保存',f'已识别 {len(entries)} 笔交易。之后同样表头自动复用。\n'+summary['validation'],parent=window)
            on_saved();window.destroy()
        except (OSError,ValueError,IndexError,KeyError) as exc:messagebox.showerror('规则未保存',str(exc),parent=window)

    ttk.Button(buttons,text='按表头行刷新',command=refresh).pack(side='left')
    ttk.Button(buttons,text='验证并保存规则',command=save).pack(side='right')
    file_box.bind('<<ComboboxSelected>>',load);sheet_box.bind('<<ComboboxSelected>>',refresh)
    load()
