-- Restore two damaged fields from the verified company_links.csv import source.
-- Match both the row identity and old value; abort if the catalog has changed.
begin;

do $repair$
declare
    changed integer;
begin
    update public.company_links
    set categories = array['ИТ-услуги']
    where id = '622270a4-9824-480d-ac00-c2366d68a090'::uuid
      and name = 'IZDAT Venture Studio'
      and url = 'https://izdat.com/'
      and categories = array[chr(65533) || chr(65533) || 'Т-услуги'];
    get diagnostics changed = row_count;
    if changed <> 1 then
        raise exception 'Expected exactly one damaged IZDAT category, got %', changed;
    end if;

    update public.company_links
    set specialization = replace(
        specialization,
        'технологи' || chr(65533) || chr(65533) || 'ми',
        'технологиями'
    )
    where id = '281452ed-194d-43e3-8f5c-e534c94410d7'::uuid
      and name = 'Unlimint'
      and url = 'https://jobs.lever.co/unlimit'
      and specialization = 'Unlimint - занимаются финансовыми технологи'
          || chr(65533) || chr(65533)
          || 'ми и предоставляют услуги в сфере электронных платежей, включая платежный шлюз, прием платежей с помощью карт, бизнес-аккаунты, выпуск карт и альтернативные методы оплаты.';
    get diagnostics changed = row_count;
    if changed <> 1 then
        raise exception 'Expected exactly one damaged Unlimint description, got %', changed;
    end if;
end;
$repair$;

commit;

select name, categories, specialization
from public.company_links
where id in (
    '622270a4-9824-480d-ac00-c2366d68a090'::uuid,
    '281452ed-194d-43e3-8f5c-e534c94410d7'::uuid
)
order by name;
