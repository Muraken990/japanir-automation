#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
2_html_generator.py

1_ir_summarizer.pyの結果を受け取り、HTMLテンプレートに挿入
IR-JsonToX.pyと同じ引数形式

使用方法:
    python 2_html_generator.py --date 20251220 --time-start 08:00 --time-end 20:00

必要なライブラリ:
    pip install jinja2 requests python-dateutil
"""

import argparse
import sys
import os
import re
from datetime import datetime
from jinja2 import Template
from openai import OpenAI

# 1_ir_summarizer.pyをインポート
try:
    # カレントディレクトリから1_ir_summarizer.pyをインポート
    import importlib.util
    spec = importlib.util.spec_from_file_location("ir_summarizer", "1_ir_summarizer.py")
    ir_summarizer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ir_summarizer)
except Exception as e:
    print(f"❌ 1_ir_summarizer.pyの読み込みに失敗: {e}")
    print("1_ir_summarizer.pyが同じディレクトリにあることを確認してください")
    sys.exit(1)


# ============================================================
# カテゴリ表示名マッピング
# ============================================================

CATEGORY_DISPLAY = {
    "tender_offer": "Tender Offer",
    "m_and_a_alliance": "M&A",
    "share_buyback": "Share Buyback",
    "dividend": "Dividend",
    "financial_summary": "Earnings",
    "business_update": "Business Update",
    "earnings_guidance": "Earnings Guidance",
    "capital_policy": "Capital Policy",
    "share_cancellation": "Share Cancellation",
    "corporate_restructuring": "Restructuring",
    "product_announcement": "Product",
    "executive_change": "Personnel",
    "sales_update": "Sales Update",
    "esg_sustainability": "ESG",
    "stock_option": "Stock Option",
    "disclosure_update": "Disclosure",
    "general_ir": "Other"
}


# ============================================================
# HTML生成クラス
# ============================================================

class HTMLGenerator:
    """
    IR情報をHTMLテンプレートに挿入するクラス
    """

    def __init__(self, template_path='japan_ir_highlights_template.html'):
        """
        初期化

        Args:
            template_path: HTMLテンプレートファイルのパス
        """
        self.template_path = template_path

        if not os.path.exists(template_path):
            raise FileNotFoundError(f"テンプレートファイルが見つかりません: {template_path}")

        # テンプレート読み込み
        with open(template_path, 'r', encoding='utf-8') as f:
            self.template_content = f.read()

        self.template = Template(self.template_content)

        # OpenAI クライアント初期化
        api_key = os.getenv('OPENAI_API_KEY')
        self.openai_client = OpenAI(api_key=api_key) if api_key else None

    def _generate_keyword_with_ai(self, summary):
        """AIで30-40文字キーワードを生成"""
        if not summary or not self.openai_client:
            return ''

        try:
            prompt = f"""Summarize this IR news in around 30 characters, max 45 characters (English).
Focus on: target company, amount, or key metric.
Examples:
- "Sells Toyota Industries ¥51.9B"
- "Operating profit +170% YoY"
- "Acquires Senkushia ¥69B"

Input: {summary}
Output: (around 30 chars, max 45, no quotes)"""

            response = self.openai_client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=60
            )

            keyword = response.choices[0].message.content.strip()
            keyword = keyword.strip('"\'')
            return keyword[:45]
        except Exception as e:
            print(f"⚠️ キーワード生成エラー: {e}")
            return ''

    def _limit_to_words(self, text, max_chars=95):
        """単語境界で短くする。省略記号は付けない。"""
        text = re.sub(r'\s+', ' ', text or '').strip()
        if len(text) <= max_chars:
            return text

        words = text.split()
        kept_words = []
        for word in words:
            candidate = ' '.join(kept_words + [word])
            if len(candidate) > max_chars:
                break
            kept_words.append(word)

        return ' '.join(kept_words) if kept_words else text[:max_chars].rstrip()

    def _remove_company_prefix(self, text, company_name):
        """上段に会社名を出しているので、見出し冒頭の重複を取り除く。"""
        text = re.sub(r'\s+', ' ', text or '').strip()
        company_name = re.sub(r'\s+', ' ', company_name or '').strip()

        if company_name and text.lower().startswith(company_name.lower()):
            text = text[len(company_name):].lstrip(' ,.-:;')

        return text[:1].upper() + text[1:] if text else text

    def _fallback_single_headline(self, summary, company_name):
        """AIが使えない場合の短縮見出し"""
        headline_source = self._remove_company_prefix(summary, company_name)
        first_sentence = re.split(r'(?<=[.!?])\s+', headline_source or '')[0]
        headline = first_sentence or headline_source
        return self._limit_to_words(headline, 95)

    def _generate_single_headline_with_ai(self, ir, company_name, ir_type):
        """単一企業画像用の短い見出しを生成"""
        summary = (ir.get('short_summary') or '').strip()
        if not summary:
            return ir_type

        if not self.openai_client:
            return self._fallback_single_headline(summary, company_name)

        try:
            prompt = f"""Rewrite this IR summary into a concise image headline.

Rules:
- 8 to 14 words.
- Max 95 characters.
- Do not repeat the company name because it is shown separately.
- Keep the key action, amount, stake, target, or metric.
- Plain English only.
- No hashtags, no quotes.

Company: {company_name}
Category: {ir_type}
Summary: {summary}

Headline:"""

            response = self.openai_client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=80,
                temperature=0.2
            )

            headline = response.choices[0].message.content.strip().strip('"\'')
            headline = self._remove_company_prefix(headline, company_name)
            return self._limit_to_words(headline, 95)
        except Exception as e:
            print(f"⚠️ 単一画像見出し生成エラー: {e}")
            return self._fallback_single_headline(summary, company_name)
    
    def generate_html(self, ir_list, date_str, output_path=None):
        """
        IR情報リストからHTMLを生成
        
        Args:
            ir_list: IR情報のリスト（1_ir_summarizer.pyから取得）
            date_str: 日付（YYYYMMDD形式）
            output_path: 出力HTMLファイルパス（省略時は自動生成）
        
        Returns:
            str: 生成されたHTMLファイルのパス
        """
        
        # 日付フォーマット: December 19, 2025
        date_obj = datetime.strptime(date_str, '%Y%m%d')
        formatted_date = date_obj.strftime('%B %d, %Y')
        
        # IR情報を整形
        formatted_ir_list = []
        for ir in ir_list:
            # AIで30文字キーワードを生成
            summary = ir.get('short_summary', '')
            keyword = self._generate_keyword_with_ai(summary)

            formatted_ir = {
                'company_name': ir['company_name'],
                'stock_code': ir['stock_code'],
                'ir_type': ir['ir_type'],
                'category_display': CATEGORY_DISPLAY.get(ir['ir_type'], 'Other'),
                'keyword': keyword,  # summaryからkeywordに変更
            }
            formatted_ir_list.append(formatted_ir)
        
        # HTMLレンダリング
        html_output = self.template.render(
            date=formatted_date,
            ir_list=formatted_ir_list
        )
        
        # 出力ファイルパス
        if output_path is None:
            output_path = f'japan_ir_highlights_{date_str}.html'
        
        # HTMLファイル保存
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html_output)
        
        print(f"✅ HTML生成完了: {output_path}")
        
        return output_path

    def generate_single_html(self, ir, date_str, time_label, output_path=None):
        """
        単一企業用HTMLを生成

        Args:
            ir: IR情報（1社分）
            date_str: 日付（YYYYMMDD形式）
            time_label: 投稿時刻ラベル（例: 20:00 JST）
            output_path: 出力HTMLファイルパス（省略時は自動生成）

        Returns:
            str: 生成されたHTMLファイルのパス
        """
        single_template_path = 'japan_ir_single_template_draft.html'

        if not os.path.exists(single_template_path):
            raise FileNotFoundError(f"テンプレートファイルが見つかりません: {single_template_path}")

        with open(single_template_path, 'r', encoding='utf-8') as f:
            single_template = Template(f.read())

        date_obj = datetime.strptime(date_str, '%Y%m%d')
        formatted_date = f"{date_obj.strftime('%B')} {date_obj.day}, {date_obj.year}"

        ticker = ir.get('stock_code') or ir.get('ticker') or ''
        company_name = ir.get('company_name', '')
        ir_type = CATEGORY_DISPLAY.get(ir.get('ir_type', ''), 'Other')
        headline = self._generate_single_headline_with_ai(ir, company_name, ir_type)

        html_output = single_template.render(
            date=formatted_date,
            time=time_label,
            ticker=ticker,
            company_name=company_name,
            ir_type=ir_type,
            headline=headline
        )

        if output_path is None:
            output_path = f'japan_ir_single_{date_str}.html'

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html_output)

        print(f"✅ 単一企業HTML生成完了: {output_path}")

        return output_path


# ============================================================
# メイン処理
# ============================================================

def main(date_str, time_start, time_end, output_path=None):
    """
    メイン処理
    
    Args:
        date_str: 日付（YYYYMMDD）
        time_start: 開始時刻（HH:MM）
        time_end: 終了時刻（HH:MM）
        output_path: 出力HTMLファイルパス
    
    Returns:
        str: 生成されたHTMLファイルのパス
    """
    print("=" * 60)
    print("🚀 HTML生成処理開始")
    print("=" * 60)
    print(f"日付: {date_str}")
    print(f"時刻範囲: {time_start} - {time_end}")
    print("")
    
    # ステップ1: 1_ir_summarizer.pyでIR情報取得
    print("📝 ステップ1: IR情報取得")
    print("-" * 60)
    
    ir_list = ir_summarizer.main(date_str, time_start, time_end)
    
    if len(ir_list) == 0:
        print("ℹ️ 該当するIR情報がありませんでした")
        return None
    
    print("")
    
    # ステップ2: HTMLテンプレートに挿入
    print("📝 ステップ2: HTMLテンプレートに挿入")
    print("-" * 60)
    
    generator = HTMLGenerator()
    html_path = generator.generate_html(ir_list, date_str, output_path)
    
    print("")
    print("=" * 60)
    print("✅ HTML生成完了")
    print("=" * 60)
    print(f"ファイル: {html_path}")
    print("")
    print("次のステップ:")
    print(f"  python 3_image_generator.py {html_path}")
    
    return html_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='IR情報からHTML生成（1_ir_summarizer.py → HTMLテンプレート）'
    )
    parser.add_argument(
        '--date',
        required=True,
        help='日付（YYYYMMDD形式）例: 20251220'
    )
    parser.add_argument(
        '--time-start',
        required=True,
        help='開始時刻（HH:MM形式）例: 08:00'
    )
    parser.add_argument(
        '--time-end',
        required=True,
        help='終了時刻（HH:MM形式）例: 20:00'
    )
    parser.add_argument(
        '-o', '--output',
        help='出力HTMLファイルパス（省略時は自動生成）'
    )
    
    args = parser.parse_args()
    
    # 引数バリデーション
    try:
        datetime.strptime(args.date, '%Y%m%d')
        datetime.strptime(args.time_start, '%H:%M')
        datetime.strptime(args.time_end, '%H:%M')
    except ValueError as e:
        print(f"❌ 引数のフォーマットエラー: {e}")
        sys.exit(1)
    
    # メイン処理実行
    html_path = main(
        args.date,
        args.time_start,
        args.time_end,
        args.output
    )
    
    sys.exit(0 if html_path else 1)
