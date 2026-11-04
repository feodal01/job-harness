-- Справочник значений строится из строк, доступных вызывающему пользователю по RLS.
begin;

create or replace function public.company_filter_options(
    p_field text,
    p_category text default null,
    p_query text default null
)
returns table(value text, link_count bigint)
language plpgsql
stable
security invoker
set search_path = ''
as $function$
begin
    if p_field is null or p_field not in ('category', 'specialization') then
        raise exception 'p_field must be category or specialization' using errcode = '22023';
    end if;

    return query
    with candidates as (
        select c.id, unnest(c.categories) as label
        from public.company_links c
        where p_field = 'category'
          and (p_category is null or c.categories @> array[p_category])

        union all

        select c.id, c.specialization as label
        from public.company_links c
        where p_field = 'specialization'
          and (p_category is null or c.categories @> array[p_category])
    )
    select options.label, count(distinct options.id)
    from candidates options
    where options.label is not null and btrim(options.label) <> ''
      and (p_query is null or strpos(lower(options.label), lower(p_query)) > 0)
    group by options.label;
end;
$function$;

revoke all on function public.company_filter_options(text, text, text) from public, anon;
grant execute on function public.company_filter_options(text, text, text) to authenticated;

notify pgrst, 'reload schema';
commit;
