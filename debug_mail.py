import imaplib
import email
import re
from email.header import decode_header

def debug_folders():
    host = "imap.163.com"
    user = "yao313996@163.com"
    password = "SLhjF3egnT7aGbKg" # 确保这是授权码
    
    client = imaplib.IMAP4_SSL(host)
    try:
        client.login(user, password)
        # 163 必须的 ID 命令
        imaplib.Commands['ID'] = ('NONAUTH', 'AUTH', 'SELECTED', 'ANY')
        client._simple_command('ID', '("name" "debug_tool")')
        
        status, folder_data = client.list()
        all_folders = [re.search(r'"([^"]+)"$', f.decode()).group(1) for f in folder_data if f]
        
        print(f"{'文件夹名称':<25} | {'未读':<4} | {'总计':<4} | {'最后一封邮件标题'}")
        print("-" * 80)
        
        for folder in all_folders:
            client.select(folder, readonly=True)
            # 搜索所有邮件
            _, all_data = client.search(None, 'ALL')
            all_ids = all_data[0].split()
            # 搜索未读邮件
            _, unseen_data = client.search(None, 'UNSEEN')
            unread_count = len(unseen_data[0].split())
            
            subject = "无邮件"
            if all_ids:
                # 抓取最后一封邮件的标题
                _, msg_data = client.fetch(all_ids[-1], '(BODY[HEADER.FIELDS (SUBJECT)])')
                raw_subj = msg_data[0][1].decode(errors='ignore').replace('Subject:', '').strip()
                # 解码中文标题
                decoded = decode_header(raw_subj)
                subject = "".join([str(p[0].decode(p[1] or 'utf-8') if isinstance(p[0], bytes) else p[0]) for p in decoded])
            
            print(f"{folder:<25} | {unread_count:<4} | {len(all_ids):<4} | {subject[:30]}")
            
    finally:
        client.logout()

if __name__ == "__main__":
    debug_folders()