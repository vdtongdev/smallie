import Example from './Example'
import { readmeClass } from './readmeClass'

export default function ReadmeVi({ marketStatus, onExample }: { marketStatus: string; onExample(text: string): void }) {
  return (
    <div className={readmeClass}>
      <h1>Hướng dẫn sử dụng smallie</h1>
      <p>smallie là trợ lý trò chuyện với bốn chuyên gia. Bạn chỉ cần hỏi. <strong>Trợ lý</strong> tự trả lời các câu hỏi
        chung (giải thích, tính toán, viết, tìm nhanh trên web khi cần thông tin mới), chuyển câu hỏi cho chuyên gia phù hợp,
        hoặc hỏi nhiều chuyên gia rồi tổng hợp câu trả lời. Nhãn phía trên mỗi câu trả lời cho biết ai đã trả lời.
        Nếu không ai làm được điều bạn yêu cầu, trợ lý sẽ nói rõ.</p>
      <ul>
        <li><strong>Trợ lý tài liệu</strong> trả lời dựa trên các tệp PDF và Word bạn tải lên, kèm trích dẫn tên tệp và số trang.</li>
        <li><strong>Trợ lý cơ sở dữ liệu</strong> trả lời dựa trên cơ sở dữ liệu 200 cổ phiếu phổ biến
          bằng cách viết và chạy câu lệnh SQL chỉ đọc. Mở mục <em>Đã dùng công cụ</em> phía trên câu trả lời để xem câu lệnh đã chạy.</li>
        <li><strong>Trợ lý nghiên cứu</strong> tra cứu một chủ đề trên web, Wikipedia và arXiv (cùng cơ sở dữ liệu cổ phiếu
          và tệp của bạn khi hữu ích); sau đó Trợ lý viết bản tóm tắt ngắn hoặc báo cáo có cấu trúc kèm nguồn.</li>
        <li><strong>Trợ lý biểu đồ</strong> vẽ biểu đồ đường, cột, vùng, tròn và phân tán từ dữ liệu cổ phiếu hoặc từ số liệu
          trong câu trả lời khác, kèm nhận xét ngắn.</li>
      </ul>
      <p>Bạn có thể viết bằng tiếng Việt hoặc tiếng Anh; câu trả lời sẽ dùng ngôn ngữ bạn đã viết.</p>
      
      <h2>Hỏi về tài liệu của bạn</h2>
      <ol>
        <li>Bấm <strong>Tải lên PDF / DOCX</strong> và chọn một hoặc nhiều tệp (tối đa 25 MB mỗi tệp).</li>
        <li>Chờ đến khi tệp hiện <em>sẵn sàng</em>. Việc xử lý mất từ vài giây đến một phút.</li>
        <li>Đặt câu hỏi. Bấm vào một ví dụ để đưa nó vào ô soạn tin:</li>
      </ol>
      <div className="flex flex-wrap gap-2">
        <Example text={"Tóm tắt các ý chính trong tài liệu của tôi."} onExample={onExample} />
        <Example text={"Hợp đồng quy định gì về việc chấm dứt?"} onExample={onExample} />
      </div>
      <p className="text-sm text-muted-foreground">Chỉ bạn mới thấy được tệp của mình. Xoá tệp bằng dấu × bên cạnh tên tệp.</p>
      
      <h2>Hỏi về cổ phiếu</h2>
      <p>Cơ sở dữ liệu gồm 200 cổ phiếu được nắm giữ nhiều nhất (Apple, Microsoft, NVIDIA, Tesla, JPMorgan, Coca-Cola…) với:</p>
      <ul>
        <li>hồ sơ công ty và chỉ số chính: ngành, lĩnh vực, vốn hoá, P/E, EPS, tỷ suất cổ tức, beta, biên độ 52 tuần, doanh thu, biên lợi nhuận;</li>
        <li>giá theo ngày trong khoảng 5 năm: mở cửa, cao nhất, thấp nhất, đóng cửa, giá đóng cửa điều chỉnh và khối lượng.</li>
      </ul>
      <p>{marketStatus}</p>
      <div className="flex flex-wrap gap-2">
        <Example text={"10 công ty có vốn hoá lớn nhất là những công ty nào?"} onExample={onExample} />
        <Example text={"NVDA, AMD và INTC biến động thế nào trong 12 tháng qua?"} onExample={onExample} />
        <Example text={"Cổ phiếu nào tăng mạnh nhất trong phiên gần nhất?"} onExample={onExample} />
        <Example text={"So sánh P/E trung bình theo từng ngành."} onExample={onExample} />
        <Example text={"Giá đóng cửa cuối mỗi tháng của Apple trong năm nay."} onExample={onExample} />
      </div>
      <p className="text-sm text-muted-foreground">Giá là dữ liệu cuối ngày từ Yahoo Finance, không phải giá trực tiếp. Dữ liệu tự động cập nhật vào mỗi tối
        ngày làm việc, sau khi thị trường Mỹ đóng cửa. Đây không phải lời khuyên đầu tư.</p>
      
      <h2>Vẽ biểu đồ</h2>
      <p>Hãy nói "vẽ biểu đồ", "biểu đồ" hoặc "đồ thị". Sau một câu trả lời từ cơ sở dữ liệu, "vẽ biểu đồ cho kết quả đó" sẽ vẽ
        ngay mà không truy vấn lại. Rê chuột lên biểu đồ để xem giá trị; bên dưới, <em>Xem dữ liệu</em> liệt kê các số liệu và
      {' '}<em>Tải ảnh PNG</em> lưu hình.</p>
      <div className="flex flex-wrap gap-2">
        <Example text={"Vẽ biểu đồ giá đóng cửa của AAPL và MSFT trong một năm qua."} onExample={onExample} />
        <Example text={"Vẽ biểu đồ vốn hóa của 10 công ty công nghệ lớn nhất."} onExample={onExample} />
      </div>
      
      <h2>Nghiên cứu một chủ đề</h2>
      <p>Hãy nói "nghiên cứu", "tìm hiểu", "tin tức mới nhất về…" hoặc yêu cầu một báo cáo. Trợ lý tìm trong nhiều nguồn
        (bạn có thể theo dõi trong mục <em>Đã dùng công cụ</em>), rồi trả lời bằng bản tóm tắt, hoặc bằng báo cáo, bảng so sánh
        hay hồ sơ công ty khi bạn yêu cầu.</p>
      <div className="flex flex-wrap gap-2">
        <Example text={"Tìm hiểu tin tức mới nhất về NVIDIA và so sánh với diễn biến giá cổ phiếu."} onExample={onExample} />
        <Example text={"Viết báo cáo ngắn về retrieval-augmented generation, kèm các bài báo gần đây."} onExample={onExample} />
      </div>
      <p className="text-sm text-muted-foreground">Nghiên cứu cần nhiều lượt gọi AI hơn các câu hỏi khác, nên chậm hơn và tốn nhiều hạn mức miễn phí hơn.</p>
      
      <h2>Cuộc trò chuyện và ghi nhớ</h2>
      <ul>
        <li><strong>+ Cuộc trò chuyện mới</strong> bắt đầu một cuộc trò chuyện mới. Các cuộc trước nằm ở thanh bên; một cuộc trò chuyện có thể hỏi cả về tài liệu lẫn cổ phiếu.</li>
        <li>Có thể hỏi tiếp: "còn 3 năm thì sao?", "hiển thị dạng bảng".</li>
        <li>smallie ghi nhớ những thông tin lâu dài bạn chia sẻ (tên, vai trò, cách trả lời bạn thích) và có thể xem lại
          các cuộc trò chuyện trước, nên bạn không cần nhắc lại. Xoá một cuộc trò chuyện sẽ xoá nó khỏi lịch sử đó.</li>
        <li>Mỗi người có tài khoản riêng: cuộc trò chuyện, tệp và ghi nhớ của bạn chỉ mình bạn thấy được.</li>
      </ul>
      
      <h2>Máy chủ model của bạn</h2>
      <table>
        <thead><tr><th>Nhà cung cấp</th><th>Trang lấy key</th><th>API endpoint</th><th>Model</th></tr></thead>
        <tbody>
          <tr><td>DeepSeek (rẻ, nhận thẻ nội địa)</td><td>platform.deepseek.com</td><td>https://api.deepseek.com/v1</td><td>deepseek-chat</td></tr>
          <tr><td>OpenAI</td><td>platform.openai.com</td><td>https://api.openai.com/v1</td><td>gpt-4o-mini</td></tr>
          <tr><td>OpenRouter</td><td>openrouter.ai</td><td>https://openrouter.ai/api/v1</td><td>tuỳ chọn</td></tr>
          <tr><td>Alibaba DashScope</td><td>dashscope.aliyun.com</td><td>https://dashscope-intl.aliyuncs.com/compatible-mode/v1</td><td>qwen-plus</td></tr>
        </tbody>
      </table>
      <p>Bất kỳ dịch vụ nào tương thích chuẩn OpenAI đều dùng được.</p>
      <p>Mở <strong>Cài đặt</strong> ở thanh bên trái rồi nhập API key, API endpoint khớp với nhà cung cấp của key, và một model. Sau đó bấm <strong>Lưu</strong> để kiểm tra kết nối và lưu.</p>
      <p className="text-sm text-muted-foreground">Dùng key của nhà cung cấp này với endpoint của nhà cung cấp khác là lỗi hay gặp nhất; ứng dụng sẽ nói thẳng điều đó.</p>
      <p className="text-sm text-muted-foreground">Key được lưu mã hoá trên máy chủ và chỉ gửi tới nhà cung cấp bạn chọn. Tin nhắn, một phần câu trả lời và tài liệu bạn tải lên cũng được chuyển thành vector tìm kiếm (embedding) qua OpenRouter, bằng tài khoản của ứng dụng.</p>
      
      
      <h2>Model và gói cấu hình</h2>
      <p>Ở chế độ nhóm, trợ lý đánh giá mỗi nhiệm vụ là <strong>dễ</strong>, <strong>vừa</strong> hoặc <strong>khó</strong>.
      {' '}<strong>Gói cấu hình</strong> trong Cài đặt rồi chọn model nào làm việc đó: model <em>nhỏ</em> cho việc nhẹ,
        model <em>tiêu chuẩn</em>, hoặc model <em>mạnh</em> cho việc khó nhất. Chat 1v1 cũng được đánh giá như vậy, mỗi tin nhắn một lần.</p>
      <ul>
        <li><strong>Tiết kiệm</strong> (chi ít hơn): nhiệm vụ dễ và vừa dùng model nhỏ, nhiệm vụ khó dùng model tiêu chuẩn.</li>
        <li><strong>Cân bằng</strong> (khuyên dùng): nhiệm vụ dễ dùng model nhỏ, vừa dùng model tiêu chuẩn, khó dùng model mạnh.</li>
        <li><strong>Nâng cao</strong> (câu trả lời tốt nhất): nhiệm vụ dễ dùng model tiêu chuẩn, vừa và khó dùng model mạnh.</li>
      </ul>
      <p>Model tiêu chuẩn là bắt buộc. Model nhỏ và model mạnh là tuỳ chọn: nếu để trống, các nhiệm vụ đó dùng model tiêu chuẩn.
        Nếu model nhỏ hoặc mạnh bị lỗi (ví dụ sai tên, hoặc nhà cung cấp giới hạn tần suất gọi), bước đó được chạy lại một lần bằng model tiêu chuẩn.
        Nút <em>Hoạt động</em> phía trên câu trả lời cho biết model nào xử lý từng nhiệm vụ và vì sao.</p>
      
      <h2>Mẹo</h2>
      <ul>
        <li>Nói rõ điều bạn cần: tên công ty hoặc mã cổ phiếu, khoảng thời gian, số lượng kết quả.</li>
        <li>Nếu chọn nhầm trợ lý, hãy nói rõ, ví dụ "theo tài liệu của tôi…" hoặc "từ cơ sở dữ liệu cổ phiếu…".</li>
        <li>Mỗi câu hỏi cần vài lượt gọi AI. Nếu gặp thông báo giới hạn tần suất, hãy chờ một phút rồi thử lại.</li>
      </ul>
    </div>
  )
}
