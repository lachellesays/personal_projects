"""
cli.py

Build the trial workbook from a show config without the web app:
    python3 cli.py shows/2026-06-06-june-2026.yaml [-o TrialData.xlsx]

Reads JOTFORM_API_KEY from the environment (or a .env file next to this script).
"""

import os
import sys
import argparse
from pathlib import Path

from core import fetch_submissions, parse_entries, transform, to_xlsx_bytes
from shows import load_show


def load_dotenv():
    env = Path(__file__).parent / '.env'
    if env.exists():
        for line in env.read_text().splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"\''))


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description='Build the 3-tab trial workbook from JotForm.')
    parser.add_argument('show', help='Path to the show YAML file')
    parser.add_argument('-o', '--output', help='Output .xlsx path (default: TrialData_<show>.xlsx)')
    args = parser.parse_args()

    api_key = os.environ.get('JOTFORM_API_KEY')
    if not api_key:
        sys.exit('Set JOTFORM_API_KEY in the environment or in trial_formatter/.env')

    show = load_show(args.show)
    print(f'Fetching submissions for {show.name} (form {show.form_id})...')
    submissions = fetch_submissions(show.form_id, api_key)
    print(f'  {len(submissions)} active submissions')

    result = transform(parse_entries(submissions, show.field_names), show)
    output = args.output or f'TrialData_{Path(args.show).stem}.xlsx'
    Path(output).write_bytes(to_xlsx_bytes(result))

    print(f'\nWritten: {output}')
    print(f'  Contact:     {len(result.contact)} handlers')
    print(f'  Balances:    {len(result.balances)} handlers')
    print(f'  Raw Results: {len(result.raw_results)} runs')
    print(f'  Scratched:   {len(show.scratched_handlers)} handlers, {len(show.scratched_dogs)} dogs')
    for w in result.warnings:
        print(f'  ⚠ {w}')


if __name__ == '__main__':
    main()
