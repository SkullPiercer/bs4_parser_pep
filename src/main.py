import logging
import re
from urllib.parse import urljoin

import requests_cache
from bs4 import BeautifulSoup
from tqdm import tqdm

from configs import configure_argument_parser, configure_logging
from constants import (
    BASE_DIR,
    EXPECTED_STATUS,
    MAIN_DOC_URL,
    PEPS_URL,
    PEP_SECTIONS
)
from outputs import control_output
from utils import get_response, find_tag


def whats_new(session):
    whats_new_url = urljoin(MAIN_DOC_URL, 'whatsnew/')
    response = get_response(session, whats_new_url)
    if response is None:
        return
    soup = BeautifulSoup(response.text, features='lxml')
    main_div = find_tag(soup, 'section', attrs={'id': 'what-s-new-in-python'})
    div_with_ul = main_div.find('div', attrs={'class': 'toctree-wrapper'})
    sections_by_python = div_with_ul.find_all(
        'li', attrs={'class': 'toctree-l1'}
    )
    results = [('Ссылка на статью', 'Заголовок', 'Редактор, автор')]
    for section in tqdm(sections_by_python):
        version_a_tag = section.find('a')
        href = version_a_tag['href']
        version_link = urljoin(whats_new_url, href)
        response = get_response(session, version_link)
        if response is None:
            continue
        soup = BeautifulSoup(response.text, 'lxml')
        h1 = find_tag(soup, 'h1')
        dl = find_tag(soup, 'dl')
        dl_text = dl.text.replace('\n', ' ')
        results.append((version_link, h1.text, dl_text))
    return results


def latest_versions(session):
    response = get_response(session, MAIN_DOC_URL)
    if response is None:
        return
    soup = BeautifulSoup(response.text, 'lxml')
    sidebar = find_tag(soup, 'div', attrs={'class': 'sphinxsidebarwrapper'})
    ul_tags = sidebar.find_all('ul')
    for ul in ul_tags:
        if 'All versions' in ul.text:
            a_tags = ul.find_all('a')
            break
    else:
        raise Exception('Ничего не нашлось')
    results = [('Ссылка на документацию', 'Версия', 'Статус')]
    pattern = r'Python (?P<version>\d\.\d+) \((?P<status>.*)\)'
    for a_tag in a_tags:
        link = a_tag['href']
        text_match = re.search(pattern, a_tag.text)
        if text_match is not None:
            version, status = text_match.groups()
        else:
            version, status = a_tag.text, ''
        results.append(
            (link, version, status)
        )

    return results


def download(session):
    downloads_url = urljoin(MAIN_DOC_URL, 'download.html')
    response = get_response(session, downloads_url)
    if response is None:
        return
    soup = BeautifulSoup(response.text, 'lxml')
    tabel = find_tag(soup, 'table', attrs={'class': 'docutils'})
    pdf_a4_tag = tabel.find('a', {'href': re.compile(r'.+pdf-a4\.zip$')})
    pdf_a4_link = pdf_a4_tag['href']
    archive_url = urljoin(downloads_url, pdf_a4_link)
    filename = archive_url.split('/')[-1]
    downloads_dir = BASE_DIR / 'downloads'
    downloads_dir.mkdir(exist_ok=True)
    archive_path = downloads_dir / filename
    response = session.get(archive_url)
    with open(archive_path, 'wb') as file:
        file.write(response.content)
    logging.info(f'Архив был загружен и сохранён: {archive_path}')


def pep(session):
    status_counter = initialize_status_counter()
    response = get_response(session, PEPS_URL)
    if response is None:
        return
    soup = BeautifulSoup(response.text, 'lxml')
    different_statuses = []
    results = [('Категория', 'Статус')]

    for section in PEP_SECTIONS:
        process_section(section, soup, session, status_counter,
                        different_statuses, results)

    log_different_statuses(different_statuses)

    for i in status_counter.items():
        results.append(i)
    results.append(('Всего', len(status_counter)))

    return results


def initialize_status_counter():
    return {
        'Accepted': 0,
        'Active': 0,
        'Deferred': 0,
        'Draft': 0,
        'Final': 0,
        'Provisional': 0,
        'Rejected': 0,
        'Superseded': 0,
        'Withdrawn': 0,
    }


def process_section(section, soup, session, status_counter, different_statuses,
                    results):
    content = soup.find('section', attrs={'id': section})
    if not content:
        return

    table_string = content.find_all('tr')
    for row in tqdm(table_string):
        process_row(row, session, status_counter, different_statuses)


def process_row(row, session, status_counter, different_statuses):
    table_status = get_table_status(row)
    link = row.find('a', attrs={'class': 'pep reference internal'})

    if link:
        pep_url = urljoin(PEPS_URL, link['href'])
        page_status = get_page_status(session, pep_url)
        update_status_counter(page_status, status_counter)
        check_status_mismatch(page_status, table_status, pep_url,
                              different_statuses)


def get_table_status(row):
    table_status = row.find('td')
    if table_status and len(table_status.text) > 1:
        return table_status.text[-1]
    return ''


def get_page_status(session, pep_url):
    response = get_response(session, pep_url)
    soup = BeautifulSoup(response.text, 'lxml')
    return soup.find('abbr')


def update_status_counter(page_status, status_counter):
    try:
        status_counter[page_status.text] += 1
    except KeyError:
        logging.error(f'Неcуществующий статус: {page_status.text}')


def check_status_mismatch(page_status, table_status, pep_url,
                          different_statuses):
    if page_status.text not in EXPECTED_STATUS.get(table_status, []):
        different_statuses.append(
            (
                f'{pep_url}\nСтатус в карточке: {page_status.text}'
                f'\nОжидаемые статусы: {EXPECTED_STATUS[table_status]}'
            )
        )


def log_different_statuses(different_statuses):
    if different_statuses:
        error_msg = 'Несовпадающие статусы:'
        for err in different_statuses:
            error_msg += f'\n{err}'
        logging.error(error_msg)


MODE_TO_FUNCTION = {
    'whats-new': whats_new,
    'latest-versions': latest_versions,
    'download': download,
    'pep': pep,
}


def main():
    configure_logging()
    logging.info('Парсер запущен!')
    arg_parser = configure_argument_parser(MODE_TO_FUNCTION.keys())
    args = arg_parser.parse_args()
    logging.info(f'Аргументы командной строки: {args}')
    session = requests_cache.CachedSession()
    if args.clear_cache:
        session.cache.clear()
    parser_mode = args.mode
    results = MODE_TO_FUNCTION[parser_mode](session)
    if results is not None:
        control_output(results, args)
    logging.info('Парсер завершил работу.')


if __name__ == '__main__':
    main()
