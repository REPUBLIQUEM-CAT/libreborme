from .models import Company, Borme, Anuncio, Person, BormeLog

from django.conf import settings
from django.db import IntegrityError
from django.utils.text import slugify
from django.utils import timezone

import bormeparser
from bormeparser.borme import BormeXML
from bormeparser.exceptions import BormeDoesntExistException
from bormeparser.regex import is_company, is_acto_cargo_entrante
from bormeparser.utils import FIRST_BORME

import calendar
import datetime
import time
import os

# FIXME:
#settings.BORME_DIR
# descarga -> parse -> import -> mueve a carpeta archive
# Problema: download_pdfs va a bajar de nuevo los archivos en tmp si ya estan procesados

from calendar import HTMLCalendar
from .models import Config


import logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

ch = logging.StreamHandler()
logger.addHandler(ch)
logger.setLevel(logging.INFO)


class LibreBormeCalendar(HTMLCalendar):

    def formatday(self, day, weekday):
        """
        Return a day as a table cell.
        """

        if day == 0:
            return '<td class="noday">&nbsp;</td>'  # day outside month
        elif self.today == datetime.date(self.year, self.month, day):
            last_modified = Config.objects.first().last_modified.date()
            if self.today == last_modified:
                return '<td class="day today"><a href="/borme/fecha/%d-%d-%d">%d</a></td>' % (self.year, self.month, day, day)
            else:
                return '<td class="day today">%d</td>' % day
        elif self.today > datetime.date(self.year, self.month, day) and weekday not in (5, 6):
            return '<td class="day %s"><a href="/borme/fecha/%d-%d-%d">%d</a></td>' % (self.cssclasses[weekday], self.year, self.month, day, day)
        else:
            return '<td class="day %s">%d</td>' % (self.cssclasses[weekday], day)

    def formatmonth(self, year, month):
        self.year, self.month = year, month
        self.today = datetime.date.today()
        return super(LibreBormeCalendar, self).formatmonth(year, month)


def _import1(borme):
    """
    borme: bormeparser.Borme
    """
    logger.info('\nBORME CVE: %s (%s)' % (borme.cve, borme.provincia))
    results = {'created_anuncios': 0, 'created_bormes': 0, 'created_companies': 0, 'created_persons': 0, 'errors': 0}

    try:
        borme_log = BormeLog.objects.get(borme_cve=borme.cve)
    except BormeLog.DoesNotExist:
        borme_log = BormeLog(borme_cve=borme.cve, path=borme.filename)

    borme_log.save()  # date_updated
    if borme_log.parsed:
        logger.warn('%s ya ha sido analizado.' % borme.cve)
        return results

    try:
        nuevo_borme = Borme.objects.get(cve=borme.cve)
    except Borme.DoesNotExist:
        nuevo_borme = Borme(cve=borme.cve, date=borme.date, url=borme.url, from_reg=borme.anuncios_rango[0],
                            until_reg=borme.anuncios_rango[1], province=borme.provincia.name, section=borme.seccion)
        #year, type, from_page, until_page, pages
        # num?, filename?
        logger.debug('Creando borme %s' % borme.cve)
        nuevo_borme.save()
        results['created_bormes'] += 1

    #import pdb; pdb.set_trace()

    borme_embed = {'cve': nuevo_borme.cve, 'url': nuevo_borme.url}
    for n, anuncio in enumerate(borme.get_anuncios(), 1):
        try:
            logger.debug('%d: Importando anuncio: %s' % (n, anuncio))
            try:
                company = Company.objects.get(name=anuncio.empresa)
            except Company.DoesNotExist:
                company = Company(name=anuncio.empresa)
                logger.debug('Creando empresa %s' % anuncio.empresa)
                results['created_companies'] += 1
            except IntegrityError:
                slug_c = slugify(anuncio.empresa)
                company = Company.objects.get(slug=slug_c)
                logger.warn('[%s] WARNING: Empresa similar. Mismo slug: %s' % (borme.cve, slug_c))
                logger.warn('[%s] %s\n[%s] %s\n' % (borme.cve, company.name, borme.cve, anuncio.empresa))
                results['errors'] += 1
            company.add_in_bormes(borme_embed)

            try:
                nuevo_anuncio = Anuncio.objects.get(id_anuncio=anuncio.id)
            except Anuncio.DoesNotExist:
                nuevo_anuncio = Anuncio(id_anuncio=anuncio.id, borme=nuevo_borme,
                                        datos_registrales=anuncio.datos_registrales)
                logger.debug('Creando anuncio %d: %s' % (anuncio.id, anuncio.empresa))
                results['created_anuncios'] += 1

            for acto in anuncio.get_borme_actos():
                logger.debug(acto.name)
                logger.debug(acto.value)
                if isinstance(acto, bormeparser.borme.BormeActoCargo):
                    for nombre_cargo, nombres in acto.cargos.items():
                        logger.debug('%s %s %d' % (nombre_cargo, nombres, len(nombres)))
                        lista_cargos = []
                        for nombre in nombres:
                            logger.debug('  %s' % nombre)
                            if is_company(nombre):
                                try:
                                    c, created = Company.objects.get_or_create(name=nombre)
                                    if created:
                                        logger.debug('Creando empresa: %s' % nombre)
                                        results['created_companies'] += 1

                                except IntegrityError:
                                    slug_c = slugify(nombre)
                                    c = Company.objects.get(slug=slug_c)
                                    logger.warn('[%s] WARNING: Empresa similar. Mismo slug: %s' % (borme.cve, slug_c))
                                    logger.warn('[%s] %s\n[%s] %s\n' % (borme.cve, c.name, borme.cve, nombre))
                                    results['errors'] += 1

                                c.anuncios.append(anuncio.id)
                                c.add_in_bormes(borme_embed)

                                cargo = {'title': nombre_cargo, 'name': c.name, 'type': 'company'}
                                if is_acto_cargo_entrante(acto.name):
                                    cargo['date_from'] = borme.date.isoformat()
                                    cargo_embed = {'title': nombre_cargo, 'name': company.name, 'date_from': borme.date.isoformat(), 'type': 'company'}
                                    c.update_cargos_entrantes([cargo_embed])
                                else:
                                     cargo['date_to'] = borme.date.isoformat()
                                     cargo_embed = {'title': nombre_cargo, 'name': company.name, 'date_to': borme.date.isoformat(), 'type': 'company'}
                                     c.update_cargos_salientes([cargo_embed])
                                c.save()
                            else:
                                try:
                                    p, created = Person.objects.get_or_create(name=nombre)
                                    if created:
                                        logger.debug('Creando persona: %s' % nombre)
                                        results['created_persons'] += 1

                                except IntegrityError:
                                    slug_p = slugify(nombre)
                                    p = Person.objects.get(slug=slug_p)
                                    logger.warn('[%s] WARNING: Persona similar. Mismo slug: %s' % (borme.cve, slug_p))
                                    logger.warn('[%s] %s\n[%s] %s\n' % (borme.cve, p.name, borme.cve, nombre))
                                    results['errors'] += 1

                                p.add_in_companies(company.name)
                                p.add_in_bormes(borme_embed)

                                cargo = {'title': nombre_cargo, 'name': p.name, 'type': 'person'}
                                if is_acto_cargo_entrante(acto.name):
                                    cargo['date_from'] = borme.date.isoformat()  # TODO: datetime.date
                                    # TODO: Ahora guarda serializado python: '[{"date_from": "2009-01-02", "name": "MOORE STEPHENS FIDELITAS AUDITORES SOCIEDAD LIMITA", "title": "Auditor"}]'
                                    cargo_embed = {'title': nombre_cargo, 'name': company.name, 'date_from': borme.date.isoformat(), 'type': 'person'}
                                    p.update_cargos_entrantes([cargo_embed])
                                else:
                                    cargo['date_to'] = borme.date.isoformat()
                                    cargo_embed = {'title': nombre_cargo, 'name': company.name, 'date_from': borme.date.isoformat(), 'type': 'person'}
                                    p.update_cargos_salientes([cargo_embed])

                                p.save()
                            lista_cargos.append(cargo)

                        kk = acto.name.replace('.', '||')
                        nuevo_anuncio.actos[kk] = lista_cargos

                    if is_acto_cargo_entrante(acto.name):
                        company.update_cargos_entrantes(lista_cargos)
                    else:
                        company.update_cargos_salientes(lista_cargos)
                else:
                    # FIXME:
                    # mongoengine.errors.ValidationError: ValidationError (Anuncio:55b37c97cf28dd2cfa8d069e) (Invalid diction
                    # ary key name - keys may not contain "." or "$" characters: ['actos'])
                    kk = acto.name.replace('.', '||')
                    nuevo_anuncio.actos[kk] = acto.value

            company.anuncios.append(anuncio.id)
            company.save()
            nuevo_anuncio.company = company
            nuevo_anuncio.save()
            nuevo_borme.anuncios.append(anuncio.id)

        except Exception as e:
            logger.error('[%s] ERROR importing anuncio %d' % (borme.cve, anuncio.id))
            logger.error('[X] %s: %s' % (e.__class__.__name__, e))
            results['errors'] += 1

    nuevo_borme.save()

    borme_log.errors = results['errors']
    borme_log.parsed = True  # FIXME: Si hay ValidationError, parsed = False
    borme_log.date_parsed = timezone.now()
    borme_log.save()
    return results


def get_borme_xml_filepath(date):
    year = str(date.year)
    month = '%02d' % date.month
    day = '%02d' % date.day
    filename = 'BORME-S-%s%s%s.xml' % (year, month, day)
    return os.path.join(settings.BORME_XML_ROOT, year, month, filename)


def get_borme_pdf_path(date):
    year = '%02d' % date.year
    month = '%02d' % date.month
    day = '%02d' % date.day
    return os.path.join(settings.BORME_PDF_ROOT, year, month, day)


def update_previous_xml(date):
    """ Dado una fecha, comprueba si el XML anterior es definitivo y si no lo es lo descarga de nuevo """
    xml_path = get_borme_xml_filepath(date)
    bxml = BormeXML.from_file(xml_path)

    try:
        prev_xml_path = get_borme_xml_filepath(bxml.prev_borme)
        prev_bxml = BormeXML.from_file(prev_xml_path)
        if prev_bxml.is_final:
            return False

        os.unlink(prev_xml_path)
    except FileNotFoundError:
        pass
    finally:
        prev_bxml = BormeXML.from_date(bxml.prev_borme)
        prev_bxml.save_to_file(prev_xml_path)

    return True


def import_borme_download(date, seccion=bormeparser.SECCION.A, download=True):
    """
    date: "2015", "2015-01", "2015-01-30", "--init"
    """
    if date == '--init':
        begin = FIRST_BORME[2009]
        end = datetime.date.today()
    else:
        date = tuple(map(int, date.split('-')))  # TODO: exception

        if len(date) == 3:  # 2015-06-02
            begin = datetime.date(*date)
            try:
                ret, _ = _import_borme_download_range2(begin, begin, seccion, download)
                return ret
            except BormeDoesntExistException:
                logger.info('It looks like there is no BORME for this date. Nothing was downloaded')
                return False
        elif len(date) == 2:  # 2015-06
            _, lastday = calendar.monthrange(*date)
            end = datetime.date(date[0], date[1], lastday)
            try:
                begin = datetime.date(date[0], date[1], 1)
                ret, _ = _import_borme_download_range2(begin, end, seccion, download)
            except BormeDoesntExistException:
                try:
                    begin = datetime.date(date[0], date[1], 2)
                    ret, _ = _import_borme_download_range2(begin, end, seccion, download)
                except BormeDoesntExistException:
                    try:
                        begin = datetime.date(date[0], date[1], 3)
                        ret, _ = _import_borme_download_range2(begin, end, seccion, download)
                    except BormeDoesntExistException:
                        begin = datetime.date(date[0], date[1], 4)
                        ret, _ = _import_borme_download_range2(begin, end, seccion, download)
            return ret

        elif len(date) == 1:  # 2015
            begin = FIRST_BORME[date[0]]
            end = datetime.date(date[0], 12, 31)

    ret, _ = _import_borme_download_range2(begin, end, seccion, download)
    return ret


def _import_borme_download_range2(begin, end, seccion, download, strict=False):
    """
    strict: Para en caso de error grave
    """
    next_date = begin
    total_results = {'created_anuncios': 0, 'created_bormes': 0, 'created_companies': 0, 'created_persons': 0, 'errors': 0}
    total_start_time = time.time()

    while next_date and next_date <= end:
        xml_path = get_borme_xml_filepath(next_date)
        try:
            bxml = BormeXML.from_file(xml_path)
        except FileNotFoundError:
            bxml = BormeXML.from_date(next_date)
            os.makedirs(os.path.dirname(xml_path), exist_ok=True)
            bxml.save_to_file(xml_path)

        # Add FileHandlers
        logpath = os.path.join(settings.BORME_LOG_ROOT, 'imports', '%02d-%02d' % (bxml.date.year, bxml.date.month))
        os.makedirs(logpath, exist_ok=True)

        fh1_path = os.path.join(logpath, '%02d_info.txt' % bxml.date.day)
        fh1 = logging.FileHandler(fh1_path)
        fh1.setLevel(logging.INFO)
        logger.addHandler(fh1)

        fh2_path = os.path.join(logpath, '%02d_error.txt' % bxml.date.day)
        fh2 = logging.FileHandler(fh2_path)
        fh2.setLevel(logging.WARNING)
        logger.addHandler(fh2)

        pdf_path = get_borme_pdf_path(bxml.date)
        os.makedirs(pdf_path, exist_ok=True)
        logger.info('============================================================')
        logger.info('Ran import_borme_download at %s' % timezone.now())
        logger.info('  Import date: %s. Section: %s' % (bxml.date.isoformat(), seccion))
        logger.info('============================================================')
        logger.info(pdf_path)

        print('\nPATH: %s\nDATE: %s\nSECCION: %s\n' % (pdf_path, bxml.date, seccion))

        bormes = []
        if download:
            _, files = bxml.download_pdfs(pdf_path, seccion=seccion)
        else:
            _, _, files = next(os.walk(pdf_path))
            files = list(map(lambda x: os.path.join(pdf_path, x), files))

        for filepath in files:
            if filepath.endswith('-99.pdf'):
                continue
            logger.info('%s' % filepath)
            try:
                bormes.append(bormeparser.parse(filepath))
            except Exception as e:
                logger.error('[X] Error grave en bormeparser.parse(): %s' % filepath)
                logger.error('[X] %s: %s' % (e.__class__.__name__, e))
                if strict:
                    logger.error('[X] Una vez arreglado, reanuda la importación:')
                    logger.error('[X]   python manage.py importbormetoday local')
                    return False, total_results

        for borme in sorted(bormes):
            start_time = time.time()
            try:
                results = _import1(borme)
            except Exception as e:
                logger.error('[%s] Error grave en _import1:' % borme.cve)
                logger.error('[%s] %s' % (borme.cve, e))
                logger.error('[%s] Prueba importar manualmente en modo detallado para ver el error:' % borme.cve)
                logger.error('[%s]   python manage.py importbormepdf %s -v 3' % (borme.cve, borme.filename))
                if strict:
                    logger.error('[%s] Una vez arreglado, reanuda la importación:' % borme.cve)
                    logger.error('[%s]   python manage.py importbormetoday local' % borme.cve)
                    return False, total_results

            total_results['created_anuncios'] += results['created_anuncios']
            total_results['created_bormes'] += results['created_bormes']
            total_results['created_companies'] += results['created_companies']
            total_results['created_persons'] += results['created_persons']
            total_results['errors'] += results['errors']

            if not all(map(lambda x: x == 0, total_results.values())):
                print_results(results, borme)
                elapsed_time = time.time() - start_time
                logger.info('[%s] Elapsed time: %.2f seconds' % (borme.cve, elapsed_time))

        # Remove handlers
        logger.removeHandler(fh1)
        logger.removeHandler(fh2)
        next_date = bxml.next_borme

    elapsed_time = time.time() - total_start_time
    logger.info('\nBORMEs creados: %d' % total_results['created_bormes'])
    logger.info('Anuncios creados: %d' % total_results['created_anuncios'])
    logger.info('Empresas creadas: %d' % total_results['created_companies'])
    logger.info('Personas creadas: %d' % total_results['created_persons'])
    logger.info('Total elapsed time: %.2f seconds' % elapsed_time)

    return True, total_results


def import_borme_pdf(filename):
    """
    Import BORME PDF to database
    """
    results = {'created_anuncios': 0, 'created_bormes': 0, 'created_companies': 0, 'created_persons': 0, 'errors': 0}

    try:
        borme = bormeparser.parse(filename)
        results = _import1(borme)
    except Exception as e:
        logger.error('[X] Error grave en bormeparser.parse(): %s' % filename)
        logger.error('[X] %s: %s' % (e.__class__.__name__, e))

    if not all(map(lambda x: x == 0, results.values())):
        print_results(results, borme)
    return True, results


def import_borme_json(filename):
    """
    Import BORME JSON to database
    """
    results = {'created_anuncios': 0, 'created_bormes': 0, 'created_companies': 0, 'created_persons': 0, 'errors': 0}

    try:
        borme = bormeparser.from_json(filename)
        results = _import1(borme)
    except Exception as e:
        logger.error('[X] Error grave en bormeparser.parse(): %s' % filename)
        logger.error('[X] %s: %s' % (e.__class__.__name__, e))

    if not all(map(lambda x: x == 0, results.values())):
        print_results(results, borme)
    return True, results


def print_results(results, borme):
    logger.info('[%s] BORMEs creados: %d' % (borme.cve, results['created_bormes']))
    logger.info('[%s] Anuncios creados: %d/%d' % (borme.cve, results['created_anuncios'], len(borme.get_anuncios())))
    logger.info('[%s] Empresas creadas: %d' % (borme.cve, results['created_companies']))
    logger.info('[%s] Personas creadas: %d' % (borme.cve, results['created_persons']))
