create or replace view dates as 
select 
--CHANGE DATES
'2026-06-06' as saturday_date,
'2026-06-07' as sunday_date
;




create or replace view SATURDAY_CLASSES_ROUGH as
SELECT 
    submission_id,
    CASE 
        -- Only attempt to parse if the string starts with '[' (a JSON array)
        WHEN saturday_classes LIKE '[%' THEN 
            replace(saturday_classes, '''', '"')::jsonb
        ELSE '[]'::jsonb 
    END as saturday_json
FROM show_data_rough;


create or replace view saturday_classes_formatted as
SELECT 
    submission_id,
    unnest_data.class_name,
    (select saturday_date from dates) as date
FROM SATURDAY_CLASSES_ROUGH,
LATERAL jsonb_array_elements_text(saturday_json) AS unnest_data(class_name);

create or replace view SUNDAY_CLASSES_ROUGH as
SELECT 
    submission_id,
    CASE 
        -- Only attempt to parse if the string starts with '[' (a JSON array)
        WHEN sunday_classes LIKE '[%' THEN 
            replace(sunday_classes, '''', '"')::jsonb
        ELSE '[]'::jsonb 
    END as sunday_json
FROM show_data_rough;


create or replace view sunday_classes_formatted as
SELECT 
    submission_id,
    unnest_data.class_name,
    (select sunday_date from dates) as date
FROM SUNDAY_CLASSES_ROUGH,
LATERAL jsonb_array_elements_text(sunday_json) AS unnest_data(class_name);


create or replace view merged_classes as 
select * 
from sunday_classes_formatted
union
select *
from saturday_classes_formatted;


create or replace view formatted_class_level as 
select a.*,
case when a.class_name ilike '%speedstakes%' then speedstakes_level else international_level end as class_level
from merged_classes a 
left join show_data_rough b 
on a.submission_id = b.submission_id
;



create or replace view formatted_name as 
SELECT 
    submission_id,
    COALESCE(handler_json->>'first', '') || ' ' || COALESCE(handler_json->>'last', '') AS full_name
FROM (
    SELECT 
        submission_id,
        CASE 
            WHEN handler_name IS NULL OR handler_name = '' THEN '{}'::jsonb
            ELSE CAST(REPLACE(handler_name, '''', '"') AS jsonb)
        END as handler_json
    FROM public.show_data_rough
) sub;






create or replace view formatted_height as
SELECT 
    submission_id,
    CASE 
        WHEN jump_height ILIKE '%regular%' THEN split_part(trim(jump_height), ' ', 1) || ' inch'
        WHEN jump_height ILIKE '%select%' THEN split_part(trim(jump_height), ' ', 1) || ' inch (s)'
        ELSE jump_height 
    END as jump_height_clean
FROM public.show_data_rough;


create or replace view formatted_handler_number as
SELECT 
    submission_id,
    ltrim(handler_number, 'H') as handler_number
FROM public.show_data_rough;

create or replace view formatted_dog_number as
SELECT 
    submission_id,
    REGEXP_REPLACE(dog_number, '^[Dd]+', '') AS dog_number
FROM public.show_data_rough;


create or replace view class_info as 
select 
a.submission_id,
'6892' as show_id,
case when class_level = 'Champion' then 'Champ' else class_level end as class_level,
case when class_name = 'Open Speedstakes 1 (all levels combined)' then 'Speedstakes' 
when class_name = 'Open Gamblers (all levels combined)' then 'Gamblers'
when class_name = 'Agility 1' then 'Agility'
when class_name = 'Jumping 1' then 'Jumping'
else class_name end as class_name,
jump_height_clean,
date
from formatted_class_level a 
left join formatted_height b 
on a.submission_id = b.submission_id;



create or replace view master_series_handling as 
SELECT 
submission_id,
    show_id,
    class_level,
    -- Use the expanded name
    expanded.final_class_name AS class_name,
    jump_height_clean,
    date
FROM (
    -- Replace 'your_table_name' with your actual table or view name
    SELECT * FROM public.class_info 
) t
CROSS JOIN LATERAL (
    -- Path A: It's NOT the Masters Series, just return the original name
    SELECT t.class_name AS final_class_name
    WHERE t.class_name != 'Masters Series'
    
    UNION ALL
    
    -- Path B: It IS the Masters Series, return two specific rows
    SELECT 'Masters Agility'
    WHERE t.class_name = 'Masters Series'
    UNION ALL
    SELECT 'Masters Jumping'
    WHERE t.class_name = 'Masters Series'
) AS expanded;


create or replace view class_data as 
select a.*,b.handler_number,
c.full_name,d.dog_number,e.dog_name,e.dog_breed,
cast('' as varchar) as SCT,
cast('' as varchar) as Faults,
cast('' as varchar) as Games_Points,
cast('' as varchar) as Time,
cast('' as varchar) as Place,
cast('' as varchar) as Level_Points,
e."Email"
from master_series_handling a 
left join formatted_handler_number b
on a.submission_id = b.submission_id
left join formatted_name c 
on a.submission_id = c.submission_id
left join formatted_dog_number d
on a.submission_id = d.submission_id 
left join show_data_rough e 
on a.submission_id = e.submission_id
where b.handler_number not in ('6272','13696','22891')
and d.dog_number not in ('26608')
;



-- create or replace view class_data_new as 
-- select *
-- from class_data
-- where submission_id > 6556971253855028113
-- ;

select *
from class_data;


create or replace view dates as 
select 
--CHANGE DATES
'2026-06-06' as saturday_date,
'2026-06-07' as sunday_date;






create or replace view contact_name as 
SELECT 
    submission_id,
    COALESCE(handler_json->>'last', '') AS last_name,
    COALESCE(handler_json->>'first', '') as first_name
    
FROM (
    SELECT 
        submission_id,
        CASE 
            WHEN handler_name IS NULL OR handler_name = '' THEN '{}'::jsonb
            ELSE CAST(REPLACE(handler_name, '''', '"') AS jsonb)
        END as handler_json
    FROM public.show_data_rough
) sub;




create or replace view contact_handler_number as
SELECT 
    submission_id,
    ltrim(handler_number, 'H') as handler_number
FROM public.show_data_rough;



create or replace view contact_address as 
SELECT 
    submission_id,
    COALESCE(address_json->>'addr_line1', '') AS addr_line1,
    COALESCE(address_json->>'addr_line2', '') as addr_line2,
    cast('' as varchar) as addr_line3,
    COALESCE(address_json->>'city', '') as city,
    upper(COALESCE(address_json->>'state', '')) as state,
    left(COALESCE(address_json->>'postal', ''),5) as postal
FROM (
    SELECT 
        submission_id,
        CASE 
            WHEN address IS NULL OR address = '' THEN '{}'::jsonb
            ELSE CAST(REPLACE(address, '''', '"') AS jsonb)
        END as address_json
    FROM public.show_data_rough
) sub;



create or replace view contact_info as 
select distinct c.handler_number,
CONCAT(UPPER(LEFT(b.last_name, 1)), SUBSTRING(b.last_name, 2))  as last_name,
CONCAT(UPPER(LEFT(b.first_name, 1)), SUBSTRING(b.first_name, 2))  as first_name,
upper(d.addr_line1) as addr_line1, 
upper(d.addr_line2) as addr_line2, 
addr_line3,
upper(d.city) as city, 

case when d.state = 'VIRGINIA' then 'VA' else d.state end as state, 

d.postal,
upper(a."Email") as "Email", 
cast('' as varchar) home, cast('' as varchar) work, cast('' as varchar) cell
from show_data_rough a 
left join contact_name b
on a.submission_id = b.submission_id 
left join contact_handler_number c 
on a.submission_id = c.submission_id 
left join contact_address d 
on a.submission_id = d.submission_id
where upper(d.addr_line1) != '2260 MILL ROAD'
and upper(d.addr_line1) != '1744 BIG ISLANE HWY'
and upper(d.addr_line2) != 'BEDFORD, VA 24523'
;


select * from contact_info
where handler_number in (select distinct handler_number from class_data)
;


-- select *
-- from contact_info
-- where handler_number in (select distinct handler_number from class_data_new)
-- ;


create or replace view balance_data_start as 
select a.submission_id,a.handler_number,
class_level, class_name,
last_name, first_name
from class_data a
left join contact_info b
on a.handler_number = b.handler_number
;


create or replace view create_balances as 
select *,
case when class_name ilike 'Master%' then 17.50 else 15.00 end as class_cost
from balance_data_start;

create or replace view create_sums as 
select handler_number,last_name,first_name,
sum(class_cost) as total_owed
from create_balances
group by 1,2,3;




create or replace view paid_stage1 as
SELECT 
    ltrim(handler_number, 'H') as handler_number,
    substring(payment_data from '"total":"([^"]+)"') AS total_amount_paid
FROM show_data_rough;



create or replace view total_paid as 
select handler_number,sum(cast(total_amount_paid as float)) as total_amount_paid
from paid_stage1 group by 1;



create or replace view balances_owed as
select a.handler_number,a.last_name,a.first_name,
(a.total_owed - b.total_amount_paid) as total_remaining_balance
from create_sums a 
left join total_paid b 
on a.handler_number = b.handler_number
;



select *
from balances_owed
where handler_number in (select distinct handler_number from class_data)

;

